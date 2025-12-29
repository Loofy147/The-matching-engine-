# This service is responsible for the detailed, second-stage re-ranking of freelancer candidates.
# It fetches an initial candidate set from the database (using the first-stage SQL query)
# and then applies more complex scoring logic in the application layer.

import json
from datetime import datetime
import geopy.distance

# Placeholder for a database connection object
db = None

def _clamp(n, lower=0.0, upper=1.0):
    """Clamps a number between a lower and upper bound."""
    return max(lower, min(n, upper))

class MatchingService:
    """
    Orchestrates the two-stage matching process.
    """
    def __init__(self, db_connection):
        self.db = db_connection

    def match_job(self, job_id, top_n=50):
        """
        Main entry point for the matching process.
        """
        initial_candidates = self._fetch_initial_candidates(job_id)
        if not initial_candidates:
            return []

        job = self.db.find_job_by_id(job_id)
        candidate_ids = [c['user_id'] for c in initial_candidates]
        candidate_profiles_map = self.db.get_user_full_profiles_batch(candidate_ids)
        weights = self.db.get_match_weights()

        reranked_results = []
        for candidate in initial_candidates:
            candidate_profile = candidate_profiles_map.get(candidate['user_id'])
            if not candidate_profile:
                continue

            time_score_details = self.compute_time_score(candidate_profile, job)
            place_score_details = self.compute_place_score(candidate_profile, job)
            cost_score_details = self.compute_cost_score(candidate_profile, job)
            experience_score_details = self.compute_experience_score(candidate_profile, job, candidate.get('experience_score', 0))

            final_score = self._aggregate_scores(
                weights,
                time_score_details,
                place_score_details,
                cost_score_details,
                experience_score_details
            )

            reranked_results.append({
                'user_id': candidate['user_id'],
                'final_score': final_score,
                'breakdown': {
                    'time': time_score_details,
                    'place': place_score_details,
                    'cost': cost_score_details,
                    'experience': experience_score_details,
                }
            })

        sorted_results = sorted(reranked_results, key=lambda x: x['final_score'], reverse=True)
        top_matches = sorted_results[:top_n]
        self.db.save_matches(job_id, top_matches)
        return top_matches

    def _fetch_initial_candidates(self, job_id):
        return self.db.execute_sql_file('matching_query.sql', {'job_id': job_id})

    def _aggregate_scores(self, weights, time, place, cost, experience):
        total_weight = sum(weights.values())
        if total_weight == 0:
            return 0
        weighted_score = (
            weights.get('time', 0) * time['score'] +
            weights.get('place', 0) * place['score'] +
            weights.get('cost', 0) * cost['score'] +
            weights.get('experience', 0) * experience['score']
        )
        return weighted_score / total_weight

    def compute_time_score(self, candidate, job):
        job_windows = job.get('schedule_requirements', {}).get('windows', [])
        freelancer_windows = candidate.get('availability', [])

        if not job_windows:
            return {'score': 0.8, 'reason': 'Job has no specific time requirements.'}

        total_required_seconds = sum(
            (datetime.fromisoformat(w['end']) - datetime.fromisoformat(w['start'])).total_seconds()
            for w in job_windows
        )
        total_overlap_seconds = 0
        for jw in job_windows:
            job_start, job_end = datetime.fromisoformat(jw['start']), datetime.fromisoformat(jw['end'])
            for fw in freelancer_windows:
                freelancer_start, freelancer_end = datetime.fromisoformat(fw['start_ts']), datetime.fromisoformat(fw['end_ts'])
                overlap_start = max(job_start, freelancer_start)
                overlap_end = min(job_end, freelancer_end)
                if overlap_end > overlap_start:
                    total_overlap_seconds += (overlap_end - overlap_start).total_seconds()

        overlap_ratio = _clamp(total_overlap_seconds / total_required_seconds if total_required_seconds > 0 else 0)

        tz_diff = abs(candidate.get('timezone_offset', 0) - job.get('timezone_offset', 0))
        tz_penalty = max(0, (tz_diff - 3) / 21)
        score = overlap_ratio * (1 - tz_penalty)

        if job.get('schedule_requirements', {}).get('type') == 'flexible':
            score = min(1.0, score + 0.15)

        return {'score': _clamp(score), 'reason': f'Overlap ratio: {overlap_ratio:.2f}, TZ penalty: {tz_penalty:.2f}'}

    def compute_place_score(self, candidate, job):
        policy = job.get('location_policy', 'remote')
        if policy == 'remote':
            score = 1.0 if candidate.get('remote_ok') else 0.0
            return {'score': score, 'reason': 'Remote policy'}

        job_point = job.get('location_point')
        freelancer_points = candidate.get('location_points', [])
        if not job_point or not freelancer_points:
            return {'score': 0.1, 'reason': 'Missing location data for onsite/hybrid job'}

        job_lat, job_lon = job_point['lat'], job_point['lon']
        min_dist_km = float('inf')
        for fp in freelancer_points:
            f_lat, f_lon = fp['point']['lat'], fp['point']['lon']
            dist = geopy.distance.distance((job_lat, job_lon), (f_lat, f_lon)).km
            if dist < min_dist_km:
                min_dist_km = dist

        radius_km = job.get('location_radius_km', 50)
        score = 0.0
        reason = 'Unknown policy'
        if policy == 'onsite':
            if min_dist_km <= radius_km:
                score = 1.0
            else:
                score = max(0, 1 - (min_dist_km - radius_km) / 200)
            reason = f'Onsite policy, distance {min_dist_km:.2f}km'
        elif policy == 'hybrid':
            remote_part = 0.6 if candidate.get('remote_ok') else 0.0
            onsite_score = max(0, 1 - min_dist_km / (radius_km * 2))
            score = remote_part + 0.4 * onsite_score
            reason = f'Hybrid policy, remote ok: {candidate.get("remote_ok")}, dist: {min_dist_km:.2f}km'

        return {'score': _clamp(score), 'reason': reason}

    def compute_cost_score(self, candidate, job):
        rate = candidate.get('hourly_rate')
        price_policy = job.get('price_policy', {})
        job_min = price_policy.get('min')
        job_max = price_policy.get('max')

        if rate is None:
            return {'score': 0.6, 'reason': 'Unknown rate'}
        if job_min is None or job_max is None:
            return {'score': 0.8, 'reason': 'Job has no defined budget'}

        if job_min <= rate <= job_max:
            score = 1.0
            reason = 'Rate is within budget'
        elif rate > job_max:
            ratio = (rate - job_max) / job_max
            score = 1 / (1 + ratio)
            reason = f'Rate is {ratio:.0%} over budget'
        else: # rate < job_min
            score = 0.95
            reason = 'Rate is below budget floor'

        return {'score': _clamp(score), 'reason': reason}

    def compute_experience_score(self, candidate, job, skill_overlap):
        job_reqs = job.get('experience_requirements', [])
        if not job_reqs:
            return {'score': 0.8, 'reason': 'Job has no specific experience requirements'}

        # Domain experience
        candidate_domains = candidate.get('domains', [])
        req_domain = job_reqs[0].get('domain')
        req_years = job_reqs[0].get('min_years', 0)
        domain_years_norm = 0
        candidate_seniority = 'junior'
        for cd in candidate_domains:
            if cd.get('domain') == req_domain:
                candidate_years = cd.get('years', 0)
                domain_years_norm = min(1.0, candidate_years / req_years if req_years > 0 else 1.0)
                candidate_seniority = cd.get('seniority', 'junior')
                break

        # Seniority match
        seniority_map = {'junior': 0.4, 'mid': 0.7, 'senior': 1.0, 'lead': 1.0}
        seniority_score = seniority_map.get(candidate_seniority, 0)

        # Certifications bonus
        certs_bonus = 0
        mandatory_certs = [flag.split(':')[1] for flag in job.get('mandatory_flags', []) if flag.startswith('cert:')]
        if mandatory_certs:
            candidate_certs = {c['cert_code'] for c in candidate.get('certs', [])}
            if all(mc in candidate_certs for mc in mandatory_certs):
                certs_bonus = 0.15

        # Final composite score
        score = (
            0.55 * skill_overlap +
            0.25 * domain_years_norm +
            0.15 * seniority_score +
            certs_bonus
        )
        reason = f'Skill overlap: {skill_overlap:.2f}, Domain years: {domain_years_norm:.2f}, Seniority: {seniority_score:.2f}'
        return {'score': _clamp(score), 'reason': reason}

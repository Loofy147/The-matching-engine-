# This service is responsible for the detailed, second-stage re-ranking of freelancer candidates.
# It now operates asynchronously, enqueuing jobs for background processing.

import json
import logging
from datetime import datetime
import geopy.distance
from cache_client import CacheClient
from job_queue import initialize_job_queue

# Placeholder for a database connection object
db = None

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def _clamp(n, lower=0.0, upper=1.0):
    """Clamps a number between a lower and upper bound."""
    return max(lower, min(n, upper))

class MatchingService:
    """
    Orchestrates the asynchronous, two-stage matching process.
    """
    def __init__(self, db_connection, cache_client):
        self.db = db_connection
        self.cache = cache_client
        self.job_queue = initialize_job_queue(self._execute_match_job_logic)

    def _get_cache_key(self, job_id):
        return f"matches:{job_id}"

    def trigger_match_job(self, job_id):
        """
        Public-facing method to trigger a matching job.
        """
        return self.job_queue.enqueue_job(job_id)

    def _execute_match_job_logic(self, job_id, top_n=50):
        """
        The core matching logic that runs in a background worker.
        """
        logging.info(f"Starting background match execution for job_id: {job_id}")
        try:
            initial_candidates = self._fetch_initial_candidates(job_id)
            if not initial_candidates:
                logging.info(f"No initial candidates found for job_id: {job_id}")
                return

            job = self.db.find_job_by_id(job_id)
            if not job:
                logging.warning(f"No job found for job_id: {job_id}")
                return

            candidate_ids = [c['user_id'] for c in initial_candidates]
            candidate_profiles_map = self.db.get_user_full_profiles_batch(candidate_ids)
            weights = self.db.get_match_weights()

            reranked_results = []
            for candidate in initial_candidates:
                candidate_profile = candidate_profiles_map.get(candidate['user_id'])
                if not candidate_profile:
                    logging.warning(f"No full profile found for candidate_id: {candidate['user_id']}")
                    continue

                time_score_details = self.compute_time_score(candidate_profile, job)
                place_score_details = self.compute_place_score(candidate_profile, job)
                cost_score_details = self.compute_cost_score(candidate_profile, job)
                experience_score_details = self.compute_experience_score(candidate_profile, job, candidate.get('experience_score', 0))

                final_score = self._aggregate_scores(weights, time_score_details, place_score_details, cost_score_details, experience_score_details)

                reranked_results.append({
                    'user_id': candidate['user_id'],
                    'final_score': final_score,
                    'breakdown': { 'time': time_score_details, 'place': place_score_details, 'cost': cost_score_details, 'experience': experience_score_details }
                })

            sorted_results = sorted(reranked_results, key=lambda x: x['final_score'], reverse=True)

            top_matches = sorted_results[:top_n]
            self.db.save_matches(job_id, top_matches)
            self.cache.set(self._get_cache_key(job_id), top_matches)
            logging.info(f"Successfully completed matching and cached results for job_id: {job_id}")

        except Exception as e:
            logging.error(f"An error occurred during the background matching process for job_id {job_id}: {e}", exc_info=True)

    def get_match_results(self, job_id):
        return self.cache.get(self._get_cache_key(job_id))

    def _fetch_initial_candidates(self, job_id):
        return self.db.execute_sql_file('matching_query.sql', {'job_id': job_id})

    def _aggregate_scores(self, weights, time, place, cost, experience):
        total_weight = sum(weights.values())
        if total_weight == 0: return 0
        return (weights.get('time', 0) * time['score'] + weights.get('place', 0) * place['score'] + weights.get('cost', 0) * cost['score'] + weights.get('experience', 0) * experience['score']) / total_weight

    def compute_time_score(self, candidate, job):
        if 'schedule_requirements' not in job or 'windows' not in job['schedule_requirements']:
            return {'score': 0.8, 'reason': 'Job has no specific time requirements.'}
        job_windows = job['schedule_requirements']['windows']
        freelancer_windows = candidate.get('availability', [])
        try:
            total_required_seconds = sum((datetime.fromisoformat(w['end']) - datetime.fromisoformat(w['start'])).total_seconds() for w in job_windows)
            total_overlap_seconds = 0
            for jw in job_windows:
                job_start, job_end = datetime.fromisoformat(jw['start']), datetime.fromisoformat(jw['end'])
                for fw in freelancer_windows:
                    freelancer_start, freelancer_end = datetime.fromisoformat(fw['start_ts']), datetime.fromisoformat(fw['end_ts'])
                    overlap_start, overlap_end = max(job_start, freelancer_start), min(job_end, freelancer_end)
                    if overlap_end > overlap_start:
                        total_overlap_seconds += (overlap_end - overlap_start).total_seconds()
        except (ValueError, TypeError) as e:
            logging.warning(f"Could not parse time windows for user {candidate.get('id')}: {e}")
            return {'score': 0.2, 'reason': 'Error parsing time data.'}
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
            return {'score': 1.0 if candidate.get('remote_ok') else 0.0, 'reason': 'Remote policy'}
        if 'location_point' not in job or 'lat' not in job['location_point'] or 'lon' not in job['location_point']:
            return {'score': 0.1, 'reason': 'Job is missing location point data.'}
        if not candidate.get('location_points'):
            return {'score': 0.1, 'reason': 'Candidate has no location data.'}
        job_lat, job_lon = job['location_point']['lat'], job['location_point']['lon']
        min_dist_km = float('inf')
        for fp in candidate['location_points']:
            try:
                f_lat, f_lon = fp['point']['lat'], fp['point']['lon']
                dist = geopy.distance.distance((job_lat, job_lon), (f_lat, f_lon)).km
                min_dist_km = min(min_dist_km, dist)
            except (KeyError, TypeError):
                logging.warning(f"Could not parse location point for user {candidate.get('id')}.")
                continue
        radius_km = job.get('location_radius_km', 50)
        score, reason = 0.0, 'Unknown policy'
        if policy == 'onsite':
            score = max(0, 1 - (min_dist_km - radius_km) / 200) if min_dist_km > radius_km else 1.0
            reason = f'Onsite policy, distance {min_dist_km:.2f}km'
        elif policy == 'hybrid':
            remote_part = 0.6 if candidate.get('remote_ok') else 0.0
            onsite_score = max(0, 1 - min_dist_km / (radius_km * 2))
            score, reason = remote_part + 0.4 * onsite_score, f'Hybrid policy, remote ok: {candidate.get("remote_ok")}, dist: {min_dist_km:.2f}km'
        return {'score': _clamp(score), 'reason': reason}

    def compute_cost_score(self, candidate, job):
        rate = candidate.get('hourly_rate')
        price_policy = job.get('price_policy', {})
        job_min, job_max = price_policy.get('min'), price_policy.get('max')
        if rate is None: return {'score': 0.6, 'reason': 'Candidate rate unknown'}
        if job_min is None or job_max is None: return {'score': 0.8, 'reason': 'Job budget not defined'}
        if job_min <= rate <= job_max: score, reason = 1.0, 'Rate is within budget'
        elif rate > job_max: score, reason = 1 / (1 + (rate - job_max) / job_max), f'Rate is {((rate/job_max)-1):.0%} over budget'
        else: score, reason = 0.95, 'Rate is below budget floor'
        return {'score': _clamp(score), 'reason': reason}

    def compute_experience_score(self, candidate, job, skill_overlap):
        job_reqs = job.get('experience_requirements', [])
        if not job_reqs: return {'score': 0.8, 'reason': 'Job has no specific experience requirements'}
        candidate_domains_map = {d['domain']: d for d in candidate.get('domains', [])}
        total_weighted_score, total_importance, primary_seniority, has_matched_domain = 0, 0, 'junior', False
        for req in job_reqs:
            req_domain, req_years, importance = req.get('domain'), req.get('min_years', 0), req.get('importance', 50)
            if not req_domain: continue
            total_importance += importance
            candidate_domain_exp = candidate_domains_map.get(req_domain)
            if candidate_domain_exp:
                if not has_matched_domain: primary_seniority = candidate_domain_exp.get('seniority', 'junior')
                has_matched_domain = True
                candidate_years = candidate_domain_exp.get('years', 0)
                total_weighted_score += min(1.0, candidate_years / req_years if req_years > 0 else 1.0) * importance
        domain_years_norm = total_weighted_score / total_importance if total_importance > 0 else 0
        seniority_map = {'junior': 0.4, 'mid': 0.7, 'senior': 1.0, 'lead': 1.0}
        seniority_score = seniority_map.get(primary_seniority, 0)
        mandatory_certs = [flag.split(':')[1] for flag in job.get('mandatory_flags', []) if flag.startswith('cert:')]
        certs_bonus = 0.15 if mandatory_certs and all(mc in {c['cert_code'] for c in candidate.get('certs', [])} for mc in mandatory_certs) else 0
        score = (0.55 * skill_overlap + 0.25 * domain_years_norm + 0.15 * seniority_score + certs_bonus)
        reason = f'Skill overlap: {skill_overlap:.2f}, Domain score: {domain_years_norm:.2f}, Seniority: {seniority_score:.2f}'
        return {'score': _clamp(score), 'reason': reason}

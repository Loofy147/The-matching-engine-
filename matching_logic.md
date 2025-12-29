# Pseudocode for Two-Stage Freelancer Matching Engine

This document outlines the logic for the application layer of the matching engine. It follows a two-stage process:
1.  **Fast Filter & Rank (in Database):** Use a comprehensive SQL query (like `matching_query.sql`) to perform an initial, broad-phase ranking on the entire dataset and retrieve a manageable subset of the most promising candidates (e.g., top 200-500).
2.  **Detailed Re-ranking (in Application):** For each candidate from the first stage, compute more complex scores for each of the four axes. This allows for logic that is difficult or inefficient to implement in pure SQL.

---

### **Main Function: `match_job(job_id, top_n)`**

```
function match_job(job_id, top_n = 50):
  // STAGE 1: Fast Filter & Rank using the database
  initial_candidates = db.execute_sql_file('matching_query.sql', {job_id: job_id});
  if not initial_candidates:
    return [];

  // Load the full job object to access its detailed requirements in memory
  job = db.find_job_by_id(job_id);

  // --- OPTIMIZATION: Prevent N+1 queries by batch-fetching all data upfront ---
  candidate_ids = [c.user_id for c in initial_candidates];
  // Fetch all required candidate profiles in a single query, returned as a map
  candidate_profiles_map = db.get_user_full_profiles_batch(candidate_ids);
  // Fetch weights configuration once
  weights = db.get_match_weights(); // returns {time: 0.20, place: 0.15, ...}
  // --- End of Optimization ---

  // STAGE 2: Detailed Re-ranking in the Application Layer
  reranked_results = [];
  for candidate in initial_candidates:
    // Retrieve the pre-fetched profile from the map
    candidate_profile = candidate_profiles_map.get(candidate.user_id);
    if not candidate_profile:
      continue; // Skip if a profile couldn't be fetched for some reason

    // Calculate detailed scores for each axis using the pre-fetched data
    time_score_details = compute_time_score(candidate_profile, job);
    place_score_details = compute_place_score(candidate_profile, job);
    cost_score_details = compute_cost_score(candidate_profile, job);
    experience_score_details = compute_experience_score(candidate_profile, job);

    // Calculate the final, re-ranked score using the pre-fetched weights
    final_score = (
      weights.time * time_score_details.score +
      weights.place * place_score_details.score +
      weights.cost * cost_score_details.score +
      weights.experience * experience_score_details.score
    );

    // Store results with detailed reasons for explainability
    reranked_results.append({
      user_id: candidate.user_id,
      final_score: final_score,
      breakdown: {
        time: time_score_details,
        place: place_score_details,
        cost: cost_score_details,
        experience: experience_score_details
      }
    });

  // Sort candidates by the new, more accurate final score
  sorted_results = sort_desc(reranked_results, key='final_score');

  // Persist the top N matches to a `job_matches` table for later retrieval
  top_matches = sorted_results.slice(0, top_n);
  db.save_matches(job_id, top_matches);

  // Return the final list of top N candidates
  return top_matches;
```

---

### **Axis Scoring Functions**

#### **1. `compute_time_score(candidate, job)`**

```
function compute_time_score(candidate, job):
  // Example logic for time matching
  // This is where you'd parse `freelancer_availability` and `job.schedule_requirements`

  // 1. Overlap Score
  job_windows = job.schedule_requirements.windows;
  candidate_windows = candidate.availability;

  required_duration = calculate_total_duration(job_windows);
  overlap_duration = calculate_overlap(candidate_windows, job_windows);

  overlap_ratio = overlap_duration / required_duration; // Clamped to [0, 1]

  // 2. Timezone Penalty
  // Assume candidate and job timezones are available
  tz_diff_hours = abs(candidate.timezone_offset - job.timezone_offset);
  // Penalty is 0 if within 3 hours, then increases linearly
  timezone_penalty = max(0, (tz_diff_hours - 3) / 21.0);

  // 3. Final Score Combination
  time_score = overlap_ratio * (1 - timezone_penalty);

  // Bonus for flexibility if the job allows it
  if job.schedule_requirements.type == 'flexible':
    time_score = min(1.0, time_score + 0.15);

  return {
    score: clamp(time_score, 0, 1),
    reason: `Overlap: ${round(overlap_ratio*100)}%, TZ Penalty: ${round(timezone_penalty*100)}%`
  };
```

#### **2. `compute_place_score(candidate, job)`**

```
function compute_place_score(candidate, job):
  // More detailed location scoring
  policy = job.location_policy;

  if policy == 'remote':
    score = 1.0 if candidate.remote_ok else 0.0;
    reason = "Candidate is remote-ready";

  elif policy == 'onsite':
    // Use Haversine formula for accurate distance calculation
    distance_km = haversine_distance(candidate.location_point, job.location_point);
    radius_km = job.location_radius_km;

    if distance_km <= radius_km:
      score = 1.0;
    else:
      // Score decreases as the candidate is further away
      score = max(0, 1 - (distance_km - radius_km) / (radius_km * 2)); // Example falloff
    reason = `Distance is ${round(distance_km)}km from job location`;

  elif policy == 'hybrid':
    // For hybrid, we can average the remote and onsite scores, possibly with a bias
    remote_score = 1.0 if candidate.remote_ok else 0.0;
    // Onsite score logic as above
    distance_km = haversine_distance(candidate.location_point, job.location_point);
    onsite_score = max(0, 1 - distance_km / 200.0); // Simple linear falloff for hybrid

    score = 0.6 * remote_score + 0.4 * onsite_score; // Example weighting
    reason = "Hybrid policy evaluated";

  return {score: clamp(score, 0, 1), reason: reason};
```

#### **3. `compute_cost_score(candidate, job)`**

```
function compute_cost_score(candidate, job):
  // Refined cost calculation
  rate = candidate.hourly_rate;
  min_budget = job.budget_floor;
  max_budget = job.budget_ceiling;

  if rate >= min_budget and rate <= max_budget:
    score = 1.0;
    reason = "Rate is within budget";
  elif rate > max_budget:
    // Use a non-linear falloff for rates above budget
    // At 2x the max budget, score is 0.5. At 3x, it's 0.33.
    score = 1 / (1 + (rate - max_budget) / max_budget);
    reason = `Rate is ${round((rate/max_budget-1)*100)}% over budget`;
  elif rate < min_budget:
    score = 0.95; // Small penalty for being potentially underqualified or a risky bid
    reason = "Rate is below budget floor";

  return {score: clamp(score, 0, 1), reason: reason};
```

#### **4. `compute_experience_score(candidate, job)`**

```
function compute_experience_score(candidate, job):
  // A composite score from multiple experience facets

  // 1. Skill Score (could be its own complex function, e.g., using embeddings)
  skill_score = calculate_skill_overlap(candidate.skills, job.required_skills);

  // 2. Domain Experience
  required_domain = job.domain;
  candidate_domain_exp = find(candidate.experience, exp => exp.domain == required_domain);
  domain_years_norm = 0;
  if candidate_domain_exp:
    domain_years_norm = min(1.0, candidate_domain_exp.years / 10.0);

  // 3. Seniority Match
  required_seniority = job.required_seniority; // e.g., 'senior'
  candidate_seniority = 'junior'; // Default
  if candidate_domain_exp:
    candidate_seniority = candidate_domain_exp.seniority;
  else:
    // If no experience in the specific domain, look for their highest seniority
    // in any other domain. This is a more graceful fallback than defaulting to 'junior'.
    highest_seniority_exp = max(candidate.experience, key=lambda exp: exp.years, default=None);
    if highest_seniority_exp:
      candidate_seniority = highest_seniority_exp.seniority;

  seniority_map = {'junior': 0.4, 'mid': 0.7, 'senior': 1.0, 'lead': 1.0};
  seniority_score = seniority_map.get(candidate_seniority, 0);

  // 4. Certification Bonus
  cert_bonus = 0;
  if job.mandatory_requirements.certs:
    if has_all_certs(candidate.certs, job.mandatory_requirements.certs):
      cert_bonus = 0.15;

  // Weighted average for the final experience score
  exp_score = (
    0.5 * skill_score +
    0.3 * domain_years_norm +
    0.2 * seniority_score
  );

  // Add bonus points, ensuring the total doesn't exceed 1
  exp_score = min(1.0, exp_score + cert_bonus);

  return {
    score: clamp(exp_score, 0, 1),
    reason: `Skills: ${round(skill_score*100)}%, Domain: ${round(domain_years_norm*100)}%, Seniority: ${round(seniority_score*100)}%`
  };
```

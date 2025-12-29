-- This SQL query is designed to find and rank the best freelancer candidates for a given job.
-- It uses a multi-stage approach with Common Table Expressions (CTEs) to calculate scores
-- across four axes: Experience, Cost, Place, and Time.

-- USAGE: Replace the placeholder ':job_id' with the actual UUID of the job you are matching.

WITH
  -- 1. Target Job: Select the job and its specific requirements
  target_job AS (
    SELECT
      j.id AS job_id,
      j.budget_floor,
      j.budget_ceiling,
      j.location_policy,
      j.location_point,
      j.location_radius_km,
      -- Extract mandatory requirements for later filtering
      (SELECT jsonb_array_elements_text(j.mandatory_requirements)) AS mandatory_req
    FROM jobs j
    WHERE j.id = :job_id -- Placeholder for the job ID
  ),

  -- 2. Initial Candidate Pool: Filter freelancers who meet mandatory requirements.
  candidate_pool AS (
    SELECT u.id AS user_id, u.hourly_rate, u.remote_ok, u.locations
    FROM users u
    -- The WHERE clause ensures that only candidates who meet all mandatory
    -- requirements are included in the matching pool.
    WHERE NOT EXISTS (
      -- Subquery to find any mandatory requirements the user does NOT meet.
      -- If this subquery returns any rows, the user is excluded.
      SELECT 1
      FROM (
        SELECT jsonb_array_elements_text(j.mandatory_requirements) AS req
        FROM jobs j WHERE j.id = :job_id
      ) AS job_reqs
      LEFT JOIN freelancer_experience fe ON u.id = fe.user_id
        AND (
          -- Match domain requirement (e.g., "domain:fintech")
          (job_reqs.req LIKE 'domain:%' AND fe.domain = substring(job_reqs.req from 8)) OR
          -- Match certification requirement (e.g., "cert:AWS")
          (job_reqs.req LIKE 'cert:%' AND fe.certifications ? substring(job_reqs.req from 6))
        )
      WHERE fe.user_id IS NULL -- This identifies a mandatory requirement that was NOT met
    )
  ),

  -- 3. Skill Score Calculation
  -- This CTE calculates a weighted skill score for each candidate based on the job's requirements.
  skill_scores AS (
    SELECT
      fs.user_id,
      -- Calculate the ratio of the user's matched skill score to the maximum possible score for the job.
      COALESCE(SUM((fs.proficiency / 100.0) * jr.importance) / NULLIF(SUM(jr.importance), 0), 0) AS skill_overlap_score
    FROM freelancer_skills fs
    JOIN job_requirements jr ON fs.skill_id = jr.skill_id
    WHERE jr.job_id = :job_id
    GROUP BY fs.user_id
  ),

  -- 4. Experience Score Calculation (incorporating skill score)
  experience_scores AS (
    SELECT
      c.user_id,
      -- Join with the calculated skill scores, defaulting to 0 if no skills match.
      COALESCE(ss.skill_overlap_score, 0) AS skill_overlap_score,

      -- Domain experience score (normalized)
      COALESCE(
        (SELECT LEAST(fe.years / 10.0, 1.0)
         FROM freelancer_experience fe
         JOIN jobs j ON j.id = :job_id
         WHERE fe.user_id = c.user_id
           AND fe.domain = j.domain -- Match against the specific job domain
        ), 0
      ) AS domain_years_norm,

      -- Seniority match score
      COALESCE(
        (SELECT
           CASE fe.seniority
             WHEN 'senior' THEN 1.0
             WHEN 'lead' THEN 1.0
             WHEN 'mid' THEN 0.7
             WHEN 'junior' THEN 0.4
             ELSE 0
           END
         FROM freelancer_experience fe
         JOIN jobs j ON j.id = :job_id
         WHERE fe.user_id = c.user_id
           AND fe.domain = j.domain -- Match against the specific job domain
        ), 0
      ) AS seniority_score
    FROM candidate_pool c
  ),

  -- 4. Cost Score Calculation
  cost_scores AS (
    SELECT
      c.user_id,
      CASE
        -- If rate is within budget, score is 1
        WHEN c.hourly_rate BETWEEN j.budget_floor AND j.budget_ceiling THEN 1.0
        -- If rate is above budget, use a non-linear falloff
        WHEN c.hourly_rate > j.budget_ceiling
          THEN 1.0 / (1 + (c.hourly_rate - j.budget_ceiling) / j.budget_ceiling)
        -- If rate is below budget, score is slightly penalized
        WHEN c.hourly_rate < j.budget_floor
          THEN 0.95
        ELSE 0
      END AS cost_score
    FROM candidate_pool c, target_job j
  ),

  -- 5. Place Score Calculation
  --    NOTE: This uses the PostGIS ST_Distance function for accurate geospatial calculations.
  --    It assumes `users.locations` is a JSONB array with objects containing 'lat' and 'lon',
  --    and that `jobs.location_point` is a GEOGRAPHY type.
  place_scores AS (
    WITH distances AS (
      SELECT
        c.user_id,
        -- Use ST_Distance for accurate distance in meters, then convert to km.
        ST_Distance(
          -- Create a geography point from the user's location JSON
          ST_SetSRID(ST_MakePoint((c.locations->0->>'lon')::float, (c.locations->0->>'lat')::float), 4326)::geography,
          j.location_point
        ) / 1000.0 AS distance_km
      FROM candidate_pool c, target_job j
      WHERE j.location_policy = 'onsite'
        AND c.locations IS NOT NULL AND jsonb_array_length(c.locations) > 0
        AND c.locations->0->>'lon' IS NOT NULL
        AND c.locations->0->>'lat' IS NOT NULL
        AND j.location_point IS NOT NULL
    )
    SELECT
      c.user_id,
      CASE
        WHEN j.location_policy = 'remote' AND c.remote_ok THEN 1.0
        WHEN j.location_policy = 'remote' AND NOT c.remote_ok THEN 0.1
        WHEN j.location_policy = 'onsite' THEN
          COALESCE(
            (SELECT
              CASE
                WHEN d.distance_km <= j.location_radius_km THEN 1.0
                -- Score falls off gradually outside the desired radius
                ELSE GREATEST(0, 1 - (d.distance_km - j.location_radius_km) / (j.location_radius_km * 4))
              END
             FROM distances d
             WHERE d.user_id = c.user_id
            ),
            0.1 -- Default score if candidate or job has no location data for an onsite role
          )
        ELSE 0.5 -- Default for hybrid
      END AS place_score
    FROM candidate_pool c, target_job j
  ),

  -- 6. Time Score Calculation (Conceptual)
  --    This provides a simplified time score based on basic availability overlap.
  --    Complex recurring schedules are best handled in the application layer.
  time_scores AS (
    SELECT
      c.user_id,
      -- Check if the freelancer has any availability window that overlaps with the job's timeframe.
      -- This gives a score of 1 if there's an overlap, 0.3 otherwise (as a default).
      COALESCE(
        (SELECT 1.0
         FROM freelancer_availability fa, target_job j
         WHERE fa.user_id = c.user_id
           AND (fa.start_time, fa.end_time) OVERLAPS (j.start_date, j.end_date)
         LIMIT 1),
        0.3
      ) AS time_score
    FROM candidate_pool c
  ),

  -- 7. Pivot Weights for Efficient Access
  pivoted_weights AS (
    SELECT
      MAX(CASE WHEN axis = 'experience' THEN weight END) AS w_experience,
      MAX(CASE WHEN axis = 'cost'       THEN weight END) AS w_cost,
      MAX(CASE WHEN axis = 'place'      THEN weight END) AS w_place,
      MAX(CASE WHEN axis = 'time'       THEN weight END) AS w_time
    FROM match_weights
  ),

  -- 8. Final Score Aggregation
  final_scores AS (
    SELECT
      c.user_id,
      -- Individual axis scores for explainability
      exp.skill_overlap_score,
      exp.domain_years_norm,
      exp.seniority_score,
      cs.cost_score,
      ps.place_score,
      ts.time_score,

      -- Weighted experience score
      (0.5 * exp.skill_overlap_score + 0.3 * exp.domain_years_norm + 0.2 * exp.seniority_score) AS experience_final_score,

      -- Weights are now available from the cross join
      w.w_experience,
      w.w_cost,
      w.w_place,
      w.w_time

    FROM candidate_pool c
    JOIN experience_scores exp ON c.user_id = exp.user_id
    JOIN cost_scores cs ON c.user_id = cs.user_id
    JOIN place_scores ps ON c.user_id = ps.user_id
    JOIN time_scores ts ON c.user_id = ts.user_id
    CROSS JOIN pivoted_weights w -- Efficiently join the single row of weights
  )

-- 9. Final Selection and Ranking
SELECT
  user_id,
  -- Calculate the final weighted score
  (
    w_experience * experience_final_score +
    w_cost * cost_score +
    w_place * place_score +
    w_time * time_score
  ) AS final_match_score,
  -- Return individual scores for explainability
  jsonb_build_object(
    'experience', jsonb_build_object('score', experience_final_score, 'reason', 'skills/domain/seniority'),
    'cost', jsonb_build_object('score', cost_score, 'reason', 'rate vs budget'),
    'place', jsonb_build_object('score', place_score, 'reason', 'location policy match'),
    'time', jsonb_build_object('score', time_score, 'reason', 'availability (placeholder)')
  ) AS reasons
FROM final_scores
ORDER BY final_match_score DESC
LIMIT 200; -- Limit to the top N candidates

-- This SQL query performs the first-stage retrieval and coarse ranking of freelancer candidates for a given job.
-- It is designed to be fast and efficient, filtering out non-viable candidates and providing a
-- reasonably-ordered list for a more detailed re-ranking in the application layer.

-- USAGE: Replace the placeholder ':job_id' with the actual UUID of the job.

WITH
  -- 1. Extract required skills and their importance from the target job's JSONB requirements.
  req_skills AS (
    SELECT
      (req->>'skill_id')::int AS skill_id,
      (req->>'importance')::int AS importance
    FROM (
      SELECT jsonb_array_elements(experience_requirements) AS req
      FROM jobs
      WHERE id = :job_id
    ) AS req_data
  ),

  -- 2. Filter freelancers who have at least one of the required skills.
  --    This significantly narrows the pool of candidates for the next stage.
  freelancer_candidate_skills AS (
    SELECT fs.user_id, fs.skill_id, fs.proficiency
    FROM freelancer_skills fs
    WHERE fs.skill_id IN (SELECT skill_id FROM req_skills)
  ),

  -- 3. Calculate the weighted skill overlap score for each candidate.
  --    This forms the core of the initial experience ranking.
  skill_overlap_scores AS (
    SELECT
      fcs.user_id,
      SUM((fcs.proficiency / 100.0) * rs.importance) AS matched_score,
      SUM(rs.importance) AS max_possible_score
    FROM freelancer_candidate_skills fcs
    JOIN req_skills rs ON rs.skill_id = fcs.skill_id
    GROUP BY fcs.user_id
  ),

  -- 4. Initial Candidate Pool: Filter freelancers based on the job's mandatory flags.
  --    This is the "hard filter" stage. A user is excluded if they fail any mandatory check.
  candidate_pool AS (
    SELECT u.id AS user_id, u.hourly_rate
    FROM users u
    -- Ensure the user appears in the skill overlap calculation as a prerequisite
    WHERE u.id IN (SELECT user_id FROM skill_overlap_scores)
      -- And the user meets all mandatory requirements defined in the job
      AND NOT EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text( (SELECT mandatory_flags FROM jobs WHERE id = :job_id) ) AS flag
        WHERE
          -- Example: flag = "cert:ISO9001". Check if the user has this certification.
          (flag LIKE 'cert:%' AND NOT EXISTS (
            SELECT 1 FROM freelancer_certs fc
            WHERE fc.user_id = u.id AND fc.cert_code = substring(flag from 6)
          )) OR
          -- Example: flag = "domain:fintech". Check if the user has this domain experience.
          (flag LIKE 'domain:%' AND NOT EXISTS (
            SELECT 1 FROM freelancer_domains fd
            WHERE fd.user_id = u.id AND fd.domain = substring(flag from 8)
          ))
          -- This section can be extended with more mandatory flag types (e.g., location).
    )
  ),

  -- 5. Combine scores for a preliminary ranking.
  --    These calculations are simplified for speed.
  preliminary_scores AS (
    SELECT
      cp.user_id,
      -- Experience Score (based on skill overlap)
      COALESCE(sos.matched_score / NULLIF(sos.max_possible_score, 0), 0) AS experience_score,

      -- Simplified Cost Score
      (
        SELECT
          CASE
            WHEN cp.hourly_rate <= (j.price_policy->>'max')::numeric THEN 1.0
            ELSE 0.7 -- Coarse penalty for being over budget, to be refined in re-ranking
          END
        FROM jobs j WHERE j.id = :job_id
      ) AS cost_score,

      -- Simplified Place Score
      (
        SELECT
          CASE
            WHEN j.location_policy = 'remote' AND u.remote_ok THEN 1.0
            WHEN j.location_policy = 'remote' AND NOT u.remote_ok THEN 0.2
            ELSE 0.8 -- Default for onsite/hybrid, to be refined by distance check in re-ranking
          END
        FROM jobs j, users u WHERE j.id = :job_id AND u.id = cp.user_id
      ) AS place_score,

      -- Simplified Time Score (placeholder, as detailed overlap is complex)
      0.5 AS time_score

    FROM candidate_pool cp
    LEFT JOIN skill_overlap_scores sos ON cp.user_id = sos.user_id
  ),

  -- 6. Fetch Axis Weights
  weights AS (
    SELECT
      MAX(CASE WHEN axis = 'experience' THEN weight END) AS w_experience,
      MAX(CASE WHEN axis = 'cost'       THEN weight END) AS w_cost,
      MAX(CASE WHEN axis = 'place'      THEN weight END) AS w_place,
      MAX(CASE WHEN axis = 'time'       THEN weight END) AS w_time
    FROM match_weights
  )

-- 7. Final Selection and Coarse Ranking
SELECT
  ps.user_id,
  ps.experience_score,
  -- Calculate the final weighted score for initial ranking
  (
    ps.experience_score * w.w_experience +
    ps.cost_score * w.w_cost +
    ps.place_score * w.w_place +
    ps.time_score * w.w_time
  ) / (w.w_experience + w.w_cost + w.w_place + w.w_time) AS initial_score
FROM preliminary_scores ps, weights w
ORDER BY initial_score DESC
LIMIT 500; -- Limit to a manageable number of candidates for the application-layer re-ranking.

-- This script contains the DDL for extending the database schema
-- based on the freelancer matching system design.

-- Note: The GEOGRAPHY type used in the 'jobs' table typically requires
-- a spatial database extension like PostGIS in PostgreSQL.
-- If you are not using PostgreSQL with PostGIS, you might need to
-- adapt this (e.g., using separate latitude and longitude columns).

-- 1. Freelancer Availability
CREATE TABLE freelancer_availability (
  user_id UUID NOT NULL REFERENCES users(id),
  start_time TIMESTAMPTZ NOT NULL,
  end_time TIMESTAMPTZ NOT NULL,
  recurrence JSONB DEFAULT '{}' , -- optional: {"type":"weekly","days":[1,3,5],"start":"09:00","end":"17:00"}
  PRIMARY KEY (user_id, start_time)
);

-- 2. User Profile Extensions for Location Preferences
ALTER TABLE users ADD COLUMN remote_ok BOOLEAN DEFAULT TRUE;
ALTER TABLE users ADD COLUMN locations JSONB; -- e.g. [{"country":"DZ","city":"Algiers","radius_km":50}]

-- 3. Detailed Freelancer Experience
CREATE TABLE freelancer_experience (
  user_id UUID NOT NULL REFERENCES users(id),
  domain TEXT NOT NULL,        -- e.g., "e-commerce","fintech"
  years NUMERIC(5,2) DEFAULT 0,
  seniority TEXT CHECK (seniority IN ('junior','mid','senior','lead')),
  certifications JSONB,
  PRIMARY KEY (user_id, domain)
);

-- 4. Freelancer Rate History (optional but good practice)
CREATE TABLE freelancer_rates (
  user_id UUID NOT NULL REFERENCES users(id),
  rate NUMERIC(12,2) NOT NULL,
  effective_from TIMESTAMPTZ NOT NULL,
  effective_to TIMESTAMPTZ,
  PRIMARY KEY (user_id, effective_from)
);

-- 5. Job Extensions for Detailed Matching Requirements
ALTER TABLE jobs ADD COLUMN start_date TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN end_date TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN schedule_requirements JSONB DEFAULT '{}' ; -- e.g., {"type":"fixed","windows":[{"start":"2025-01-10T09:00Z","end":"2025-01-10T17:00Z"}]}
ALTER TABLE jobs ADD COLUMN location_policy TEXT DEFAULT 'remote' CHECK (location_policy IN ('remote','onsite','hybrid'));
ALTER TABLE jobs ADD COLUMN location_point GEOGRAPHY(POINT); -- for onsite/hybrid jobs
ALTER TABLE jobs ADD COLUMN location_radius_km INT DEFAULT 50; -- acceptable radius for onsite
ALTER TABLE jobs ADD COLUMN budget_floor NUMERIC(12,2);
ALTER TABLE jobs ADD COLUMN budget_ceiling NUMERIC(12,2);
ALTER TABLE jobs ADD COLUMN mandatory_requirements JSONB DEFAULT '[]'; -- e.g. ["cert:X","domain:fintech"]

-- 6. Match Weights Configuration Table
CREATE TABLE match_weights (
  axis TEXT PRIMARY KEY, -- 'time','place','cost','experience'
  weight NUMERIC(4,3) NOT NULL
);

-- Default weights as a starting point
INSERT INTO match_weights (axis, weight) VALUES
('time', 0.20),
('place', 0.15),
('cost', 0.30),
('experience', 0.35);

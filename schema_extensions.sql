-- This script contains the DDL for extending the database schema
-- based on the freelancer matching system design document.

-- Note: The GEOGRAPHY type requires the PostGIS extension in PostgreSQL.

-- 1. Taxonomy Tables for Axes and Attributes
CREATE TABLE axes (
  axis_id TEXT PRIMARY KEY, -- 'time','place','cost','experience'
  description TEXT
);

CREATE TABLE axis_attributes (
  attr_id TEXT PRIMARY KEY, -- 'availability','timezone','remote_ok', etc.
  axis_id TEXT NOT NULL REFERENCES axes(axis_id),
  data_type TEXT NOT NULL, -- 'boolean','numeric','jsonb','geography','timestamp'
  description TEXT
);

-- 2. Freelancer-side Extensions

-- Availability windows for freelancers
CREATE TABLE freelancer_availability (
  user_id UUID NOT NULL REFERENCES users(id),
  start_ts TIMESTAMPTZ NOT NULL,
  end_ts TIMESTAMPTZ NOT NULL,
  recurrence JSONB DEFAULT '{}', -- e.g., {"type":"weekly","days":[1,3,5]}
  PRIMARY KEY(user_id, start_ts)
);

-- Location and remote work preferences for users
ALTER TABLE users ADD COLUMN remote_ok BOOLEAN DEFAULT TRUE;
-- Storing multiple potential work locations for a freelancer
ALTER TABLE users ADD COLUMN location_points JSONB; -- e.g. [{"point":"SRID=4326;POINT(lon lat)","radius_km":50}]

-- Domain-specific experience for freelancers
CREATE TABLE freelancer_domains (
  user_id UUID NOT NULL REFERENCES users(id),
  domain TEXT NOT NULL,
  years NUMERIC(5,2) DEFAULT 0,
  PRIMARY KEY(user_id, domain)
);

-- Certifications held by freelancers
CREATE TABLE freelancer_certs (
  user_id UUID NOT NULL REFERENCES users(id),
  cert_code TEXT NOT NULL,
  issued_by TEXT,
  verified BOOLEAN DEFAULT FALSE,
  PRIMARY KEY(user_id, cert_code)
);

-- 3. Job-side Extensions

-- Scheduling requirements for jobs
ALTER TABLE jobs ADD COLUMN schedule_requirements JSONB DEFAULT '{}';
-- e.g. {"type":"fixed","windows":[{"start":"2026-01-10T09:00Z","end":"2026-01-10T17:00Z"}]}

-- Location policy and details for jobs
ALTER TABLE jobs ADD COLUMN location_policy TEXT DEFAULT 'remote' CHECK (location_policy IN ('remote','onsite','hybrid'));
ALTER TABLE jobs ADD COLUMN location_point GEOGRAPHY(POINT);
ALTER TABLE jobs ADD COLUMN location_radius_km INT DEFAULT 50;

-- Pricing and budget details for jobs
ALTER TABLE jobs ADD COLUMN price_policy JSONB DEFAULT '{}';
-- e.g. {"currency":"USD","min":50,"max":120,"negotiable":true}

-- Detailed experience requirements for jobs
ALTER TABLE jobs ADD COLUMN experience_requirements JSONB DEFAULT '[]';
-- e.g. [{"domain":"fintech","min_years":3,"importance":80}]

-- Hard filters/mandatory flags for jobs
ALTER TABLE jobs ADD COLUMN mandatory_flags JSONB DEFAULT '[]';
-- e.g. ["cert:ISO9001","location:Algeria"]

-- 4. Matching Configuration and Results Tables

-- Base weights for each matching axis
CREATE TABLE match_weights (
  axis TEXT PRIMARY KEY,
  weight NUMERIC(5,4) NOT NULL
);

-- Default weights as a starting point
INSERT INTO match_weights (axis, weight) VALUES
('time', 0.20),
('place', 0.15),
('cost', 0.30),
('experience', 0.35);

-- Job-specific overrides for axis weights
CREATE TABLE weight_overrides (
  job_id UUID REFERENCES jobs(id),
  axis TEXT,
  weight NUMERIC(5,4),
  PRIMARY KEY(job_id, axis)
);

-- Table to store and explain match results
CREATE TABLE matches (
  id BIGSERIAL PRIMARY KEY,
  job_id UUID NOT NULL REFERENCES jobs(id),
  freelancer_id UUID NOT NULL REFERENCES users(id),
  score NUMERIC(6,4) NOT NULL,
  axis_breakdown JSONB NOT NULL, -- {"time":{"score":0.8,"reason":"..."}, "place":{...}}
  created_at TIMESTAMPTZ DEFAULT now()
);

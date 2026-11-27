-- D-253 Bots inbox: a run's answer as data (proposal, checks, movers) and a person's dismissal. Additive only.
-- {"version": 1, "eda_checks": [...], "headline": "...", "proposals": [...], "movers": [...], "checks": [...], "unverified": [...]}
ALTER TABLE bots.runs ADD COLUMN IF NOT EXISTS report JSONB;
ALTER TABLE bots.runs ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ;
ALTER TABLE bots.runs ADD COLUMN IF NOT EXISTS dismissed_by TEXT;
CREATE INDEX IF NOT EXISTS runs_created_idx ON bots.runs (created_at DESC);

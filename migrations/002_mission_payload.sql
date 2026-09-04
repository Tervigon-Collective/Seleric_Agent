-- Durable mission payload + route for GET after restart
ALTER TABLE missions ADD COLUMN IF NOT EXISTS route TEXT;
ALTER TABLE missions ADD COLUMN IF NOT EXISTS result_json JSONB;
ALTER TABLE missions ADD COLUMN IF NOT EXISTS raw_json JSONB;

CREATE INDEX IF NOT EXISTS idx_missions_route ON missions(route);
CREATE INDEX IF NOT EXISTS idx_missions_updated ON missions(updated_at DESC);

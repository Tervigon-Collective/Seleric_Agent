-- Planned periods the owner has explained (ads deliberately paused, a sale, a known outage). A breach inside one is
-- shown as information with its reason, never as an alert; it also stays out of the history used to seed targets.
CREATE TABLE IF NOT EXISTS monitor.known_periods (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL,
    start_day DATE NOT NULL,
    end_day DATE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('ads_paused', 'sale', 'outage', 'other')),
    reason TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_day >= start_day)
);
CREATE INDEX IF NOT EXISTS idx_known_periods_brand ON monitor.known_periods(brand_id, start_day, end_day);

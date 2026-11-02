-- Unified business monitoring (docs/monitoring/00_PLAN.md): targets, evaluations, findings, labels.
-- Own schema so it never collides with the conversation tables in public. Metric ids are catalogue ids stored
-- as data: no metric name is hardcoded in code.
CREATE SCHEMA IF NOT EXISTS monitor;

-- What is watched and how. One row per catalogue metric; thresholds live per brand in target_versions.
CREATE TABLE IF NOT EXISTS monitor.metrics (
    metric_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    -- Which way is bad. 'none' = watch only, never alert (a metric nobody classified must not alert).
    direction_bad TEXT NOT NULL CHECK (direction_bad IN ('down', 'up', 'both', 'none')),
    -- day = judge the day's value; trailing_7d_pooled = ratio of 7-day sums (noisy ratios); hour_pace = intraday pace.
    eval_window TEXT NOT NULL CHECK (eval_window IN ('day', 'trailing_7d_pooled', 'hour_pace')),
    intraday BOOLEAN NOT NULL DEFAULT FALSE,
    -- Days after which the value stops moving (COGS and returns settle late); newer days are 'provisional'.
    settles_after_days INTEGER NOT NULL DEFAULT 0 CHECK (settles_after_days >= 0),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Append-only target history. The current target is the newest row per (brand_id, metric_id): see targets_current.
CREATE TABLE IF NOT EXISTS monitor.target_versions (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL,
    metric_id TEXT NOT NULL REFERENCES monitor.metrics(metric_id),
    floor_value DOUBLE PRECISION,
    target_value DOUBLE PRECISION,
    ceiling_value DOUBLE PRECISION,
    seed TEXT NOT NULL CHECK (seed IN ('history', 'owner')),
    seed_window_start DATE,
    seed_window_end DATE,
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (floor_value IS NULL OR target_value IS NULL OR floor_value <= target_value),
    CHECK (target_value IS NULL OR ceiling_value IS NULL OR target_value <= ceiling_value),
    CHECK (floor_value IS NULL OR ceiling_value IS NULL OR floor_value <= ceiling_value)
);
CREATE INDEX IF NOT EXISTS idx_target_versions_current
    ON monitor.target_versions(brand_id, metric_id, id DESC);

CREATE OR REPLACE VIEW monitor.targets_current AS
SELECT DISTINCT ON (brand_id, metric_id) *
FROM monitor.target_versions
ORDER BY brand_id, metric_id, id DESC;

-- One row per metric per evaluation run: what the Ops page and the agent read.
CREATE TABLE IF NOT EXISTS monitor.evaluations (
    id BIGSERIAL PRIMARY KEY,
    run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    brand_id BIGINT NOT NULL,
    metric_id TEXT NOT NULL REFERENCES monitor.metrics(metric_id),
    grain TEXT NOT NULL CHECK (grain IN ('hour', 'day')),
    period_start TIMESTAMPTZ NOT NULL,
    observed DOUBLE PRECISION,
    expected DOUBLE PRECISION,
    expected_lo DOUBLE PRECISION,
    expected_hi DOUBLE PRECISION,
    target_version_id BIGINT REFERENCES monitor.target_versions(id),
    status TEXT NOT NULL CHECK (status IN (
        'ok', 'below_floor', 'below_target', 'above_ceiling', 'above_target', 'untrusted', 'no_target', 'no_data')),
    -- Probability the true value is beyond the breached line (not a bare z-score).
    p_beyond DOUBLE PRECISION CHECK (p_beyond IS NULL OR (p_beyond >= 0 AND p_beyond <= 1)),
    impact_low DOUBLE PRECISION,
    impact_high DOUBLE PRECISION,
    data_status TEXT NOT NULL CHECK (data_status IN ('fresh', 'stale', 'partial', 'provisional')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_evaluations_latest
    ON monitor.evaluations(brand_id, metric_id, grain, period_start DESC, run_at DESC);

-- Findings with a lifecycle. An entity-level finding names the entity; a brand-level one uses entity_type 'brand'.
CREATE TABLE IF NOT EXISTS monitor.findings (
    finding_id TEXT PRIMARY KEY,
    brand_id BIGINT NOT NULL,
    metric_id TEXT REFERENCES monitor.metrics(metric_id),
    entity_type TEXT NOT NULL DEFAULT 'brand',
    entity_id TEXT NOT NULL DEFAULT '',
    -- target_gap, pace, trend, data_event (trust gate, bulk-return lump, ads off), ...
    detector TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open', 'ongoing', 'resolved')),
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'warning', 'info')),
    confidence DOUBLE PRECISION CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    impact_low DOUBLE PRECISION,
    impact_high DOUBLE PRECISION,
    summary TEXT NOT NULL,
    diagnosis JSONB,
    recommendation JSONB,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_findings_open
    ON monitor.findings(brand_id, state, severity, updated_at DESC);

CREATE TABLE IF NOT EXISTS monitor.finding_events (
    id BIGSERIAL PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES monitor.findings(finding_id) ON DELETE CASCADE,
    at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    from_state TEXT,
    to_state TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_finding_events_finding ON monitor.finding_events(finding_id, at);

-- Human feedback, the tuning signal.
CREATE TABLE IF NOT EXISTS monitor.labels (
    id BIGSERIAL PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES monitor.findings(finding_id) ON DELETE CASCADE,
    label TEXT NOT NULL CHECK (label IN ('useful', 'noise', 'wrong_action')),
    note TEXT NOT NULL DEFAULT '',
    labelled_by TEXT NOT NULL,
    labelled_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_labels_finding ON monitor.labels(finding_id, labelled_at);

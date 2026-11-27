-- Live ads monitor (services/live_monitor). Additive only: three new tables and one new nullable column.
-- Findings reuse monitor.findings / monitor.finding_events with detector = 'live.<platform>.<rule_id>'.

-- Per-entity baselines, recomputed nightly from platform history. weekday NULL = all days; 0 = Monday.
CREATE TABLE IF NOT EXISTS monitor.live_baselines (
    tenant_id TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('account', 'campaign', 'adset', 'ad')),
    entity_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    weekday SMALLINT CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6),
    window_days INT NOT NULL,
    p10 DOUBLE PRECISION,
    p50 DOUBLE PRECISION,
    p90 DOUBLE PRECISION,
    mean DOUBLE PRECISION,
    n INT NOT NULL,
    method TEXT NOT NULL,
    extra JSONB,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_live_baselines
    ON monitor.live_baselines(tenant_id, level, entity_id, metric, COALESCE(weekday, -1));

-- Small per-tenant state: heartbeat, guardrails, tracking reference, last snapshot, cadence clocks, miss counters.
CREATE TABLE IF NOT EXISTS monitor.live_state (
    tenant_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, key)
);

-- One-click actions: the previewed plan a person approves, and what executing it returned.
CREATE TABLE IF NOT EXISTS monitor.live_action_requests (
    request_id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES monitor.findings(finding_id) ON DELETE CASCADE,
    action_id TEXT NOT NULL,
    plan JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('previewed', 'executing', 'executed', 'failed', 'expired')),
    requested_by TEXT NOT NULL,
    approved_by TEXT,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    executed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_live_action_requests_finding ON monitor.live_action_requests(finding_id, created_at DESC);

ALTER TABLE monitor.finding_events ADD COLUMN IF NOT EXISTS details JSONB;

-- SUPERSEDED 2026-11-03: the owner chose Postgres (seleric_agent-postgres-1, schema `monitor`). See migrations/016_monitor_schema.sql. Kept for reference only; do not run.
-- monitor schema for the unified monitoring system (V1: brand 20, targets only).
-- STATUS: PREPARED, NOT APPLIED. Needs a ClickHouse login that can CREATE DATABASE and a writer account.
-- The read-only `cube_serve` user cannot run this. Plain MergeTree tables only (no S3, no Kafka engines).
-- Metric ids are catalogue ids stored as data, never hardcoded in code.

CREATE DATABASE IF NOT EXISTS monitor;

-- Editable targets. Latest `version` per (brand_id, metric_id) wins.
CREATE TABLE IF NOT EXISTS monitor.targets
(
    brand_id          Int64,
    metric_id         String,
    direction_bad     LowCardinality(String),   -- down | up | both | none (none = watch only, never alert)
    floor             Nullable(Float64),
    target            Nullable(Float64),
    ceiling           Nullable(Float64),
    eval_window       LowCardinality(String),   -- day | trailing_7d_pooled | hour_pace
    seed              LowCardinality(String),   -- history | owner
    seed_window_start Nullable(Date),
    seed_window_end   Nullable(Date),
    note              String,
    version           UInt64,                   -- unix millis at write
    updated_by        String,
    updated_at        DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (brand_id, metric_id);

-- One row per metric per evaluation run (the monitoring state the UI reads).
CREATE TABLE IF NOT EXISTS monitor.evaluations
(
    run_ts        DateTime('UTC'),
    brand_id      Int64,
    metric_id     String,
    grain         LowCardinality(String),       -- hour | day
    period_start  DateTime('UTC'),
    observed      Float64,
    expected      Nullable(Float64),
    expected_lo   Nullable(Float64),
    expected_hi   Nullable(Float64),
    status        LowCardinality(String),       -- ok | below_floor | below_target | above_ceiling | untrusted | no_target
    p_beyond      Nullable(Float64),            -- posterior probability the true value is beyond the breached line
    impact_low    Nullable(Float64),            -- rupees, 7-day run-rate basis
    impact_high   Nullable(Float64),
    data_status   LowCardinality(String),       -- fresh | stale | partial | provisional
    details       String                        -- JSON: inputs, parts, versions of the target used
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(run_ts)
ORDER BY (brand_id, metric_id, run_ts)
TTL toDateTime(run_ts) + INTERVAL 400 DAY;

-- Findings with lifecycle. Latest `version` per finding_id wins.
CREATE TABLE IF NOT EXISTS monitor.findings
(
    finding_id     String,
    brand_id       Int64,
    metric_id      String,
    entity_type    LowCardinality(String),      -- brand | campaign | adset | ad | product | state | cohort
    entity_id      String,
    detector       LowCardinality(String),
    state          LowCardinality(String),      -- open | ongoing | resolved
    severity       LowCardinality(String),      -- critical | warning | info
    confidence     Float64,
    impact_low     Nullable(Float64),
    impact_high    Nullable(Float64),
    opened_at      DateTime('UTC'),
    updated_at     DateTime('UTC'),
    summary        String,
    diagnosis      String,                      -- JSON from the diagnosis engine
    recommendation String,                      -- JSON from the playbooks
    version        UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (brand_id, finding_id);

-- Human labels (useful | noise | wrong_action), the feedback loop.
CREATE TABLE IF NOT EXISTS monitor.labels
(
    finding_id  String,
    label       LowCardinality(String),
    note        String,
    labelled_by String,
    labelled_at DateTime64(3, 'UTC')
)
ENGINE = MergeTree
ORDER BY (finding_id, labelled_at);

-- Admin to run separately (names are examples; password handling is the admin's):
--   CREATE USER monitor_writer IDENTIFIED BY '<set by admin>' HOST IP '<agent host>';
--   GRANT INSERT, SELECT ON monitor.* TO monitor_writer;
--   GRANT SELECT ON serve.* TO monitor_writer;

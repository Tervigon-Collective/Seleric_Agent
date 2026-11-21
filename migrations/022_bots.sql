-- D-244 Bots, Phase 1 (read-only): bot registry, triggers, runs and the activity log the right sidebar reads.
-- Additive only. Watch/webhook triggers, skills and Hindsight banks come in Phase 2 with their own migration.

CREATE SCHEMA IF NOT EXISTS bots;

CREATE TABLE IF NOT EXISTS bots.bots (
    bot_id TEXT PRIMARY KEY,
    brand_id BIGINT NOT NULL DEFAULT 20,
    name TEXT NOT NULL,
    template TEXT NOT NULL DEFAULT 'custom',
    role TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    instructions TEXT NOT NULL DEFAULT '',
    instructions_version INT NOT NULL DEFAULT 1,
    -- read-only Seleric MCP tools this bot gets on top of the agent's base set
    tools JSONB NOT NULL DEFAULT '[]'::jsonb,
    limits JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INT NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Every saved instruction text, so a run can say exactly which version it followed.
CREATE TABLE IF NOT EXISTS bots.instruction_versions (
    bot_id TEXT NOT NULL REFERENCES bots.bots(bot_id) ON DELETE CASCADE,
    version INT NOT NULL,
    body TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bot_id, version)
);

CREATE TABLE IF NOT EXISTS bots.triggers (
    trigger_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots.bots(bot_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('cron', 'signal')),
    -- cron: {"cron": "0 9 * * *", "timezone": "Asia/Kolkata"}
    -- signal: {"detector_prefix": "live.meta.", "severities": ["critical","warning"]}
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    next_run_at TIMESTAMPTZ,
    cursor BIGINT,  -- signal: last monitor.finding_events.id seen
    last_fired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bot_triggers_due ON bots.triggers(next_run_at) WHERE enabled;

CREATE TABLE IF NOT EXISTS bots.runs (
    run_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots.bots(bot_id) ON DELETE CASCADE,
    trigger_id TEXT,
    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('manual', 'chat', 'cron', 'signal')),
    reason TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'partial', 'failed', 'skipped')),
    -- one run per (trigger, scheduled slot / finding): a duplicate signal or a second scheduler tick is a no-op
    dedupe_key TEXT UNIQUE,
    -- triggers that arrived while this run was still queued (merged instead of queuing another run)
    merged JSONB NOT NULL DEFAULT '[]'::jsonb,
    instructions_version INT,
    mission_id TEXT,
    outcome TEXT,
    answer TEXT,
    error TEXT,
    tool_calls INT NOT NULL DEFAULT 0,
    requested_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bot_runs_bot ON bots.runs(bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bot_runs_queued ON bots.runs(created_at) WHERE status = 'queued';

-- What the right sidebar shows, one row per step of a run.
CREATE TABLE IF NOT EXISTS bots.activity (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES bots.runs(run_id) ON DELETE CASCADE,
    bot_id TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind TEXT NOT NULL CHECK (kind IN ('trigger', 'tool', 'answer', 'note')),
    tool TEXT,
    target TEXT,
    scope TEXT,
    decision TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('attempted', 'denied', 'failed', 'completed', 'skipped')),
    summary TEXT NOT NULL DEFAULT '',
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_bot_activity_bot ON bots.activity(bot_id, id DESC);

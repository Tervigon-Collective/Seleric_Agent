CREATE TABLE IF NOT EXISTS missions (
    mission_id TEXT PRIMARY KEY,
    user_query TEXT NOT NULL,
    normalized_query TEXT,
    status TEXT NOT NULL,
    mission_lead TEXT,
    active_specialist TEXT,
    leadership_epoch INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mission_events (
    event_id BIGSERIAL PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
    task_id TEXT,
    agent_id TEXT,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mission_events_mission_created
    ON mission_events(mission_id, created_at);

CREATE TABLE IF NOT EXISTS evidence_artifacts (
    evidence_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
    source TEXT NOT NULL,
    metric_or_fact TEXT NOT NULL,
    value_json JSONB NOT NULL,
    dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL,
    quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_mission
    ON evidence_artifacts(mission_id);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
    claim_type TEXT NOT NULL,
    text TEXT NOT NULL,
    support_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    contradiction_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    trust_label TEXT NOT NULL,
    gate_status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS leadership_transfers (
    transfer_id BIGSERIAL PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
    epoch INTEGER NOT NULL,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_refs JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

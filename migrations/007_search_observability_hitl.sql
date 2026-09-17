-- Phase 7: scoped search, observability diagnostics, and human approval controls.
ALTER TABLE threads ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(metadata::text, ''))
    ) STORED;
ALTER TABLE message_parts ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(content::text, '') || ' ' || coalesce(metadata::text, ''))
    ) STORED;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(normalized_content, '') || ' ' || coalesce(content::text, ''))
    ) STORED;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(artifact_type, '') || ' ' || coalesce(payload::text, ''))
    ) STORED;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(status, '') || ' ' || coalesce(metadata::text, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_threads_search ON threads USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_message_parts_search ON message_parts USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_memories_search ON memories USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_artifacts_search ON artifacts USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_runs_search ON runs USING GIN(search_vector);

CREATE OR REPLACE VIEW search_documents AS
SELECT t.id, 'thread'::text AS kind, coalesce(t.title, 'Untitled thread') AS title,
       coalesce(t.metadata::text, '') AS snippet, t.id AS thread_id, NULL::text AS run_id,
       t.workspace_id, t.owner_user_id, t.updated_at AS created_at,
       jsonb_build_object('status', t.status) AS metadata, t.search_vector
FROM threads t WHERE t.status <> 'DELETED'
UNION ALL
SELECT m.id, 'message', initcap(lower(m.role)) || ' message',
       string_agg(mp.content::text, ' ' ORDER BY mp.position), m.thread_id, m.run_id,
       t.workspace_id, t.owner_user_id, m.created_at, '{}'::jsonb,
       to_tsvector('simple', string_agg(mp.content::text, ' ' ORDER BY mp.position))
FROM messages m JOIN threads t ON t.id=m.thread_id
JOIN message_parts mp ON mp.message_id=m.id
WHERE t.status <> 'DELETED'
GROUP BY m.id, m.role, m.thread_id, m.run_id, t.workspace_id, t.owner_user_id, m.created_at
UNION ALL
SELECT mem.id, 'memory', initcap(lower(mem.type)), mem.normalized_content,
       mem.thread_id, mem.source_run_id, mem.workspace_id, mem.owner_user_id, mem.updated_at,
       jsonb_build_object('status', mem.status, 'provenance', mem.provenance), mem.search_vector
FROM memories mem WHERE mem.deleted_at IS NULL AND mem.status <> 'DELETED'
UNION ALL
SELECT a.id, 'artifact', coalesce(a.payload->>'title', a.artifact_type), a.payload::text,
       a.thread_id, a.run_id, a.workspace_id, coalesce(t.owner_user_id, r.requested_by_user_id),
       a.created_at, jsonb_build_object('artifact_type', a.artifact_type), a.search_vector
FROM artifacts a LEFT JOIN threads t ON t.id=a.thread_id LEFT JOIN runs r ON r.id=a.run_id
WHERE coalesce(t.owner_user_id, r.requested_by_user_id) IS NOT NULL
UNION ALL
SELECT r.id, 'run', 'Run ' || initcap(lower(r.status)), r.metadata::text, r.thread_id, r.id,
       r.workspace_id, r.requested_by_user_id, r.created_at,
       jsonb_build_object('status', r.status), r.search_vector
FROM runs r;

-- Optional vector support never blocks the required lexical path.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pgvector unavailable; search remains lexical + recency';
    END;
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') THEN
        BEGIN
            EXECUTE 'ALTER TABLE threads ADD COLUMN IF NOT EXISTS embedding vector(1536)';
            EXECUTE 'ALTER TABLE message_parts ADD COLUMN IF NOT EXISTS embedding vector(1536)';
            EXECUTE 'ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding vector(1536)';
            EXECUTE 'ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS embedding vector(1536)';
            EXECUTE 'ALTER TABLE runs ADD COLUMN IF NOT EXISTS embedding vector(1536)';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pgvector columns unavailable; continuing safely';
        END;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    action_type TEXT NOT NULL,
    action_preview JSONB NOT NULL,
    required_role TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'REQUESTED','APPROVED','REJECTED','EXPIRED','CANCELLED','EXECUTED','ROLLED_BACK'
    )),
    idempotency_key TEXT NOT NULL,
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    checkpoint_resume_token TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(workspace_id, owner_user_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS approval_decision_events (
    id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor_principal_id TEXT NOT NULL,
    reason TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS approval_rollbacks (
    id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
    actor_principal_id TEXT NOT NULL,
    action JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS telemetry_loss (
    id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT,
    component TEXT NOT NULL,
    lost_count BIGINT NOT NULL DEFAULT 1,
    reason TEXT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_approval_owner_status
    ON approval_requests(workspace_id, owner_user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_approval_events
    ON approval_decision_events(approval_id, created_at);

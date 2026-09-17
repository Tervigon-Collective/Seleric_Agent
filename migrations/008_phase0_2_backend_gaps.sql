-- Phase 0-2 backend hardening: soft deletion, mutable placeholders, and explicit events.
ALTER TABLE threads ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE run_events ADD COLUMN IF NOT EXISTS thread_sequence BIGINT;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS actor_type TEXT;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS actor_id TEXT;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS parent_event_id TEXT;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS duration_ms BIGINT;

WITH numbered AS (
    SELECT id, ROW_NUMBER() OVER (
        PARTITION BY thread_id ORDER BY created_at, run_id, sequence, id
    ) AS value
    FROM run_events
)
UPDATE run_events
SET thread_sequence = numbered.value
FROM numbered
WHERE run_events.id = numbered.id AND run_events.thread_sequence IS NULL;

ALTER TABLE run_events ALTER COLUMN thread_sequence SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_thread_sequence
    ON run_events(thread_id, thread_sequence);
CREATE INDEX IF NOT EXISTS idx_threads_owner_active_page
    ON threads(workspace_id, owner_user_id, updated_at DESC, id DESC)
    WHERE status <> 'DELETED';
CREATE INDEX IF NOT EXISTS idx_messages_thread_newest
    ON messages(thread_id, created_at DESC, id DESC);

ALTER TABLE mission_events ADD COLUMN IF NOT EXISTS source_seq BIGINT;
UPDATE mission_events
SET source_seq = NULLIF((payload->>'seq')::BIGINT, 0)
WHERE source_seq IS NULL AND (payload->>'seq') ~ '^[0-9]+$';
CREATE UNIQUE INDEX IF NOT EXISTS idx_mission_events_source_sequence
    ON mission_events(mission_id, source_seq) WHERE source_seq IS NOT NULL;

-- Phase 6: permissioned memory, faithful summaries, and context provenance.
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    project_id TEXT,
    thread_id TEXT REFERENCES threads(id) ON DELETE SET NULL,
    scope TEXT NOT NULL CHECK (scope IN ('USER', 'PROJECT', 'THREAD', 'EPISODIC')),
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING_CONSENT',
    content JSONB NOT NULL,
    normalized_content TEXT NOT NULL,
    structured_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    salience DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (salience BETWEEN 0 AND 1),
    source_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    source_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    supersedes_id TEXT REFERENCES memories(id) ON DELETE SET NULL,
    superseded_by_id TEXT REFERENCES memories(id) ON DELETE SET NULL,
    consented_at TIMESTAMPTZ,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    requires_confirmation BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (scope <> 'THREAD' OR thread_id IS NOT NULL),
    CHECK (scope NOT IN ('PROJECT', 'EPISODIC') OR project_id IS NOT NULL),
    CHECK (status <> 'ACTIVE' OR consented_at IS NOT NULL),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

ALTER TABLE thread_summaries ADD COLUMN IF NOT EXISTS owner_user_id TEXT;
ALTER TABLE thread_summaries ADD COLUMN IF NOT EXISTS covered_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE thread_summaries ADD COLUMN IF NOT EXISTS source_evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE thread_summaries ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'extractive';
ALTER TABLE thread_summaries ADD COLUMN IF NOT EXISTS version TEXT NOT NULL DEFAULT 'phase6-v1';

CREATE TABLE IF NOT EXISTS memory_preferences (
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    opted_out BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, owner_user_id)
);

CREATE TABLE IF NOT EXISTS run_memory_usage (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memories_owner_status
    ON memories(workspace_id, owner_user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_project
    ON memories(workspace_id, owner_user_id, project_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_thread
    ON memories(workspace_id, owner_user_id, thread_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_normalized
    ON memories(workspace_id, owner_user_id, normalized_content);
CREATE INDEX IF NOT EXISTS idx_memories_validity
    ON memories(valid_from, valid_to, expires_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_memories_pinned
    ON memories(workspace_id, owner_user_id, pinned) WHERE pinned AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_memories_evidence
    ON memories USING GIN(source_evidence_ids);
CREATE INDEX IF NOT EXISTS idx_run_memory_usage_owner
    ON run_memory_usage(workspace_id, owner_user_id, run_id);
CREATE INDEX IF NOT EXISTS idx_thread_summaries_owner
    ON thread_summaries(workspace_id, owner_user_id, thread_id, created_at DESC);

-- pgvector is an optional enhancement. Insufficient privileges, a missing
-- extension package, or any other extension error must not block migration.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pgvector unavailable; continuing with lexical memory retrieval';
    END;
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        BEGIN
            EXECUTE 'ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding vector(1536)';
            EXECUTE 'CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw
                     ON memories USING hnsw (embedding vector_cosine_ops)
                     WHERE deleted_at IS NULL';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pgvector indexing unavailable; continuing without vector retrieval';
        END;
    END IF;
END $$;

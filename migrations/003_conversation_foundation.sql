-- Phase 1: normalized durable conversation foundation.
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    project_id TEXT,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS thread_participants (
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id, user_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    requested_by_user_id TEXT NOT NULL,
    mission_id TEXT REFERENCES missions(mission_id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    current_attempt INTEGER NOT NULL DEFAULT 0 CHECK (current_attempt >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    user_id TEXT,
    role TEXT NOT NULL,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    parent_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS message_parts (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    part_type TEXT NOT NULL,
    content JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (message_id, position)
);

CREATE TABLE IF NOT EXISTS run_attempts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    status TEXT NOT NULL DEFAULT 'RUNNING',
    error_code TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (run_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS run_events (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    owner_user_id TEXT,
    actor_user_id TEXT,
    sequence BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'USER',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, sequence)
);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    storage_uri TEXT,
    checksum_sha256 TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    mission_id TEXT REFERENCES missions(mission_id) ON DELETE SET NULL,
    thread_id TEXT REFERENCES threads(id) ON DELETE SET NULL,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS thread_summaries (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    through_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    summary TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE missions ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE missions ADD COLUMN IF NOT EXISTS owner_user_id TEXT;
ALTER TABLE missions ADD COLUMN IF NOT EXISTS thread_id TEXT;
ALTER TABLE missions ADD COLUMN IF NOT EXISTS run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_threads_owner_updated
    ON threads(workspace_id, owner_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_thread_participants_user
    ON thread_participants(workspace_id, user_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_created
    ON messages(thread_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_runs_thread_created
    ON runs(thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_mission ON runs(mission_id);
CREATE INDEX IF NOT EXISTS idx_run_attempts_run ON run_attempts(run_id, attempt_number);
CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence ON run_events(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_attachments_thread ON attachments(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_mission ON artifacts(mission_id, created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_thread ON artifacts(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_thread_summaries_thread
    ON thread_summaries(thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_missions_owner
    ON missions(workspace_id, owner_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_missions_thread ON missions(thread_id);

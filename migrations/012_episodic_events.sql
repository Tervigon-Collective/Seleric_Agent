CREATE TABLE IF NOT EXISTS episodic_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    project_id TEXT,
    thread_id TEXT,
    run_id TEXT,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('DECISION', 'ATTEMPT', 'OUTCOME', 'ERROR', 'RETRY', 'ROLLBACK')
    ),
    summary TEXT NOT NULL,
    entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    supersedes_id TEXT,
    superseded_by_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_episodic_events_owner_created
    ON episodic_events(workspace_id, owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_events_thread
    ON episodic_events(thread_id) WHERE thread_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodic_events_run
    ON episodic_events(run_id) WHERE run_id IS NOT NULL;

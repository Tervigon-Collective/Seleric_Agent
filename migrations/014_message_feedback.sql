-- Sprint 4: append-only reader feedback on assistant answers.
CREATE TABLE IF NOT EXISTS message_feedback (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    run_id TEXT,
    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    note TEXT NOT NULL DEFAULT '',
    supersedes_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_message_feedback_message
    ON message_feedback(message_id, workspace_id, owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_message_feedback_thread
    ON message_feedback(thread_id, workspace_id, owner_user_id, created_at DESC);

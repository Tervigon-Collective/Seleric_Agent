-- Transactional persistence hardening. All constraints are introduced NOT VALID
-- and validated explicitly to avoid table-rewrite/access-exclusive-lock migrations.

CREATE TABLE IF NOT EXISTS submission_outbox (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);
CREATE INDEX IF NOT EXISTS idx_submission_outbox_pending
    ON submission_outbox(created_at, run_id) WHERE published_at IS NULL;

-- Migration 010 conservatively defaulted pre-existing rows to PENDING. A legacy
-- READY row must never remain downloadable without evidence of a clean scan.
UPDATE attachments
SET status='FAILED',
    scan_status='UNAVAILABLE',
    scan_detail=CASE
        WHEN scan_detail='' THEN 'legacy READY attachment requires re-upload and scan'
        ELSE scan_detail
    END
WHERE status='READY' AND scan_status <> 'CLEAN';

ALTER TABLE attachments VALIDATE CONSTRAINT attachments_scan_status_values;
ALTER TABLE attachments VALIDATE CONSTRAINT attachments_ready_requires_clean_scan;

CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_rollbacks_approval
    ON approval_rollbacks(approval_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_threads_id_workspace_owner
    ON threads(id, workspace_id, owner_user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_id_thread_workspace
    ON messages(id, thread_id, workspace_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_runs_id_thread_workspace_owner
    ON runs(id, thread_id, workspace_id, requested_by_user_id);

DO $$ BEGIN
    ALTER TABLE messages ADD CONSTRAINT messages_parent_same_tenant
        FOREIGN KEY (parent_message_id, thread_id, workspace_id)
        REFERENCES messages(id, thread_id, workspace_id) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE runs ADD CONSTRAINT runs_thread_same_tenant
        FOREIGN KEY (thread_id, workspace_id, requested_by_user_id)
        REFERENCES threads(id, workspace_id, owner_user_id) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE attachments ADD CONSTRAINT attachments_thread_same_tenant
        FOREIGN KEY (thread_id, workspace_id, owner_user_id)
        REFERENCES threads(id, workspace_id, owner_user_id) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE messages VALIDATE CONSTRAINT messages_parent_same_tenant;
ALTER TABLE runs VALIDATE CONSTRAINT runs_thread_same_tenant;
ALTER TABLE attachments VALIDATE CONSTRAINT attachments_thread_same_tenant;

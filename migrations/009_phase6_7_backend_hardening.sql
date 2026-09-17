-- Phase 6-7 hardening: vector population, immutable audit, and expiry operations.

CREATE INDEX IF NOT EXISTS idx_memories_expiry
    ON memories(workspace_id, owner_user_id, expires_at)
    WHERE status='ACTIVE' AND expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_approval_expiry
    ON approval_requests(expires_at)
    WHERE status IN ('REQUESTED', 'APPROVED') AND expires_at IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_approval_requested_event
    ON approval_decision_events(approval_id)
    WHERE from_status IS NULL AND to_status='REQUESTED';

INSERT INTO approval_decision_events
    (id, approval_id, from_status, to_status, actor_principal_id, metadata, created_at)
SELECT approval_requests.id || ':requested', approval_requests.id, NULL, 'REQUESTED',
       approval_requests.owner_user_id, '{"audit_kind":"REQUESTED","backfilled":true}'::jsonb,
       approval_requests.created_at
FROM approval_requests
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION reject_audit_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS immutable_run_events ON run_events;
CREATE TRIGGER immutable_run_events
    BEFORE UPDATE OR DELETE ON run_events
    FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
DROP TRIGGER IF EXISTS immutable_approval_events ON approval_decision_events;
CREATE TRIGGER immutable_approval_events
    BEFORE UPDATE OR DELETE ON approval_decision_events
    FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
DROP TRIGGER IF EXISTS immutable_approval_rollbacks ON approval_rollbacks;
CREATE TRIGGER immutable_approval_rollbacks
    BEFORE UPDATE OR DELETE ON approval_rollbacks
    FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();

-- This view is created only when pgvector exists. Application code treats its
-- absence as a normal lexical-search deployment.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') THEN
        EXECUTE $view$
            CREATE OR REPLACE VIEW search_document_embeddings AS
            SELECT id, 'thread'::text AS kind, embedding FROM threads
            UNION ALL
            SELECT DISTINCT ON (message_id) message_id, 'message'::text, embedding
                FROM message_parts WHERE embedding IS NOT NULL
                ORDER BY message_id, position
            UNION ALL
            SELECT id, 'memory'::text, embedding FROM memories
            UNION ALL
            SELECT id, 'artifact'::text, embedding FROM artifacts
            UNION ALL
            SELECT id, 'run'::text, embedding FROM runs
        $view$;
    END IF;
END $$;

CREATE OR REPLACE VIEW backend_capabilities AS
SELECT
    TRUE AS hybrid_search,
    EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') AS vector_search,
    TRUE AS approval_audit,
    TRUE AS immutable_audit,
    TRUE AS scheduled_expiry,
    TRUE AS compensating_rollback;

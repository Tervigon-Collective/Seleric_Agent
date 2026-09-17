-- Phase 5: secure attachments and durable artifact provenance.
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS classification TEXT NOT NULL DEFAULT 'ui';
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$ BEGIN
    ALTER TABLE attachments
        ADD CONSTRAINT attachments_checksum_sha256_format
        CHECK (checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$')
        NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_attachments_owner
    ON attachments(workspace_id, owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_evidence_ids
    ON artifacts USING GIN(evidence_ids);

-- Phase 5 hardening: persist fail-closed malware scan state.
ALTER TABLE attachments
    ADD COLUMN IF NOT EXISTS scan_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE attachments
    ADD COLUMN IF NOT EXISTS scan_detail TEXT NOT NULL DEFAULT '';

DO $$ BEGIN
    ALTER TABLE attachments
        ADD CONSTRAINT attachments_scan_status_values
        CHECK (scan_status IN ('PENDING', 'CLEAN', 'QUARANTINED', 'FAILED', 'UNAVAILABLE'))
        NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE attachments
        ADD CONSTRAINT attachments_ready_requires_clean_scan
        CHECK (
            status <> 'READY'
            OR (
                scan_status = 'CLEAN'
                AND storage_uri IS NOT NULL
                AND checksum_sha256 IS NOT NULL
            )
        )
        NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

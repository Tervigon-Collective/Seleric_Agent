-- Phase 4: durable attempts, leases, retries, and cancellation.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ;

ALTER TABLE run_attempts ADD COLUMN IF NOT EXISTS worker_id TEXT;
ALTER TABLE run_attempts ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE run_attempts ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE run_attempts ADD COLUMN IF NOT EXISTS retryable BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE run_attempts ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_run_attempts_recovery
    ON run_attempts(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_runs_retry
    ON runs(status, next_retry_at);

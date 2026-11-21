-- D-244 Bots, Phase 2: watch + webhook triggers, skills, per-bot delivery settings, memory/delivery activity rows.
-- Additive: widened CHECKs and new columns/tables only; Phase 1 code keeps working against it.

ALTER TABLE bots.triggers DROP CONSTRAINT IF EXISTS triggers_kind_check;
ALTER TABLE bots.triggers ADD CONSTRAINT triggers_kind_check CHECK (kind IN ('cron', 'signal', 'watch', 'webhook'));
-- webhook: sha256 of the secret shown once at creation; the secret itself is never stored
ALTER TABLE bots.triggers ADD COLUMN IF NOT EXISTS secret_hash TEXT;
-- watch: {"fingerprint": "...", "checked_at": "...", "error": "..."} from the last check
ALTER TABLE bots.triggers ADD COLUMN IF NOT EXISTS state JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE bots.runs DROP CONSTRAINT IF EXISTS runs_trigger_kind_check;
ALTER TABLE bots.runs ADD CONSTRAINT runs_trigger_kind_check
    CHECK (trigger_kind IN ('manual', 'chat', 'cron', 'signal', 'watch', 'webhook'));

ALTER TABLE bots.activity DROP CONSTRAINT IF EXISTS activity_kind_check;
ALTER TABLE bots.activity ADD CONSTRAINT activity_kind_check
    CHECK (kind IN ('trigger', 'tool', 'answer', 'note', 'memory', 'delivery'));

-- B19: {"email": ["name or address from the email contacts", ...]}
ALTER TABLE bots.bots ADD COLUMN IF NOT EXISTS notify JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Operator-written skills: named instruction blocks added to every run's brief while enabled.
CREATE TABLE IF NOT EXISTS bots.skills (
    skill_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots.bots(bot_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    body TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    author TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bot_id, name)
);

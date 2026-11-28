-- D-257 Bots: a skill carries the tools it needs, and remembers which library skill it was copied from. Additive only.
ALTER TABLE bots.skills ADD COLUMN IF NOT EXISTS tools JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE bots.skills ADD COLUMN IF NOT EXISTS library TEXT;

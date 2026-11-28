-- D-260 Bots: a library skill follows the library (config/bots/skills) until someone edits it for the bot. Additive only.
-- Existing rows default to edited (their own copy, as before); the deploy marks copies that still equal the library.
ALTER TABLE bots.skills ADD COLUMN IF NOT EXISTS edited BOOLEAN NOT NULL DEFAULT TRUE;

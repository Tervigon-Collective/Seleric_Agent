-- V3 mission artifacts (evidence, findings, plans, turn records), written in the
-- background by persistence/v3_artifacts.py. Kept apart from `artifacts` (the
-- conversation platform's table): V3 mission ids, thread ids of /v1/missions
-- sessions and run ids are not rows of missions/threads/runs, so the foreign keys
-- there would reject them. The whole artifact is one JSON document; it is only
-- ever looked up by id, mission or thread.
CREATE TABLE IF NOT EXISTS v3_artifacts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    mission_id TEXT,
    thread_id TEXT,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_v3_artifacts_mission ON v3_artifacts(mission_id, created_at);
CREATE INDEX IF NOT EXISTS idx_v3_artifacts_thread ON v3_artifacts(thread_id, created_at DESC);

-- Slim-loop rebuild (Phase 2): Meta write actions with human approvals and the LangGraph
-- checkpointer are gone, so their tables go too.
DROP TABLE IF EXISTS approval_rollbacks CASCADE;
DROP TABLE IF EXISTS approval_decision_events CASCADE;
DROP TABLE IF EXISTS approval_requests CASCADE;
DROP TABLE IF EXISTS checkpoint_writes CASCADE;
DROP TABLE IF EXISTS checkpoint_blobs CASCADE;
DROP TABLE IF EXISTS checkpoints CASCADE;
DROP TABLE IF EXISTS checkpoint_migrations CASCADE;

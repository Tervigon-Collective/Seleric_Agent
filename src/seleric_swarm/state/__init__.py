"""V3 runtime state stores (Profile A — Sprint 1 scaffolding).

See ``docs/refactor/01_PROFILE_RUNTIME.md`` §Builds. These replace the
LangGraph-era Blackboard/ad hoc mission dict with explicit, typed stores.
In-memory implementations only for now — durability (Postgres-backed, via
``conversations/postgres.py``'s existing patterns) is a Sprint 3 decision,
not assumed here.
"""

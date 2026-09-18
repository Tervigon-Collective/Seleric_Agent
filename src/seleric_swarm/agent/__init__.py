"""Seleric V3 agent runtime (Profile A — Sprint 1 scaffolding).

See ``docs/refactor/01_PROFILE_RUNTIME.md`` and ``docs/refactor/CONTRACTS.md``.
Nothing here is wired into production traffic yet — ``orchestration/dispatch.py``
still owns 100% of routing per the strangler-fig migration rule
(``docs/refactor/00_OVERVIEW.md`` §5).
"""

"""Seleric V3 agent runtime (Profile A).

See ``docs/refactor/01_PROFILE_RUNTIME.md`` and ``docs/refactor/CONTRACTS.md``.
Live traffic stays on swarm_v2 unless ``settings.v3_agent_enabled`` is True,
in which case ``agent/runner.py`` owns conversations and ``POST /v1/missions``.
"""

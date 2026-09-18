"""V3 evals harness scaffolding (Profile A — Sprint 1).

See ``docs/refactor/01_PROFILE_RUNTIME.md`` §Builds ("Pydantic Evals harness
+ golden dataset ... seeded from the existing ``eval/datasets/lookup_commerce.jsonl``
and the replay fixtures already in ``tests/replay/``").

Scope note (be honest about what Sprint 1 actually delivers): this is the
*loader*, not a runnable scorer. ``pydantic_evals`` (or a hand-rolled runner)
gets wired once there's a real agent output to score against — Sprint 1's
agent is a fixed-output stub (``agent/agent.py``), so running an eval against
it today would only prove the stub returns its own hardcoded text, not
anything about the system under test. Wiring execution is Sprint 2+ work,
tracked in ``docs/refactor/TASK_SHEET.md``.
"""

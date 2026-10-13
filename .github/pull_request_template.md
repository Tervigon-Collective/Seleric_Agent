## Summary

<!-- What changed and why. -->

## LangSmith experiment

<!-- Required when changing agent prompts (`src/seleric_swarm/agent/instructions.py`, `INSTRUCTIONS_VERSION`). -->

- Experiment URL / id:
- Baseline compared:
- Gates: schema 100% / numeric exact-match 100% / classify ≥ 95%

## Test plan

- [ ] `uv run python scripts/lint_semantic_literals.py`
- [ ] `uv run pytest -q`
- [ ] `python3 eval/regress/run_regression.py eval/regress/test_agent6.json <out> 1` if prompts or the agent loop changed (live)
- [ ] No credentials in logs or committed files

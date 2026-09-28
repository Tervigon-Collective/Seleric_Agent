## Summary

<!-- What changed and why. -->

## LangSmith experiment

<!-- Required when changing agent prompts (`src/seleric_swarm/agent/instructions.py`, `INSTRUCTIONS_VERSION`). -->

- Experiment URL / id:
- Baseline compared:
- Gates: schema 100% / numeric exact-match 100% / classify ≥ 95%

## Test plan

- [ ] `uv run pytest -q`
- [ ] `uv run pytest tests/unit/test_v3_golden_dataset.py -q` if prompts or the agent loop changed
- [ ] No credentials in logs or committed files

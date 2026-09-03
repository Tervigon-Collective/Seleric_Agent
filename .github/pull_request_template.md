## Summary

<!-- What changed and why. -->

## LangSmith experiment

<!-- Required when changing pinned prompt versions (`config/prompt_versions.yaml` or `prompts/**`). -->

- Experiment URL / id:
- Baseline compared:
- Gates: schema 100% / numeric exact-match 100% / classify ≥ 95%

## Test plan

- [ ] `uv run pytest -q`
- [ ] `uv run python -m seleric_swarm.eval.cli lookup_v1` if prompts or lookup workflow changed
- [ ] No credentials in logs or committed files

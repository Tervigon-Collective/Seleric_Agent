# `contracts.pipeline`

## Purpose

Frozen typed models at every pipeline stage boundary (Part B of the modular refactor).
Stages pass these objects only — never `dict[str, Any]`.

## Interface

Import from the package root only:

```python
from seleric_swarm.contracts.pipeline import (
    QuestionSpec,
    dump,
    load,
    fingerprint,
)
```

Public surface (`__all__`): Part B models, plus `dump`, `load`, `fingerprint`.
Private modules (`_question`, `_plan`, `_results`, `_answer`, `_errors`) must not be imported by callers.

## Flag

None. Contracts are always available; consumers gate behaviour with their own module flags.

## Failure policy

N/A (pure data). Validation failures raise pydantic `ValidationError` (e.g. `extra="forbid"`).

## Rules

- Imports: stdlib and pydantic only.
- All models: `frozen=True`, `extra="forbid"`, `schema_version` where the plan requires it.
- Breaking a frozen model after tag `contracts-v1` requires an ADR and a new schema version.

## Tests

```bash
uv run pytest tests/unit/contracts/test_models.py -q
```

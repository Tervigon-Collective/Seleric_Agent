# `spec`

## Purpose

Compile a `QuestionDraft` (phrases and window expressions) into a validated
`QuestionSpec` using value candidates and the semantic read model. Pure code —
no I/O, no question-text regex.

## Interface

```python
from seleric_swarm.spec import compile_question_spec, scope_from_spec, SpecCandidates
```

- `compile_question_spec(draft, candidates, read_model, prior, as_of) -> QuestionSpec | Clarification`
- `scope_from_spec(spec) -> RequiredScope`

## Flag

`QUERY_SPEC_MODE` (`off` | `shadow` | `enforce`, default from settings). Wiring
belongs in `pipeline/`, not `agent/runner.py`.

## Failure policy

Invalid ids, carry failures, or ambiguous named values → `Clarification` naming
the problem. Never a silent partial spec.

## Tests

```bash
uv run pytest tests/unit/spec/ -q
```

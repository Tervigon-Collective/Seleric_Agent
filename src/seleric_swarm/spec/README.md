# `spec`

## Purpose

Compile a `QuestionDraft` (phrases and window expressions) into a validated
`QuestionSpec` using value candidates and the semantic read model. Pure code —
no I/O, no question-text regex.

## Interface

```python
from seleric_swarm.spec import compile_question_spec, scope_from_spec, SpecCandidates
```

- `compile_question_spec(draft, candidates, read_model, prior, as_of, *, concepts=None, clarify_mode="enforce") -> QuestionSpec | Clarification`
  - `concepts`: the catalogue's resolution of each measure phrase (`{words: ConceptResolution}`).
  - `clarify_mode`: `CLARIFY_MODE`. The pipeline passes the setting (default `off`); called directly the compiler asks.
- `scope_from_spec(spec) -> RequiredScope`

## Flags

- `QUERY_SPEC_MODE` (`off` | `shadow` | `enforce`, default from settings). Wiring belongs in
  `pipeline/`, not `agent/runner.py`.
- `CLARIFY_MODE` (M1.4, default `off`): `enforce` returns the question, `shadow` records
  `clarify_shadow:<reason>` in `spec.assumptions` and carries on, `off` ignores it.

## Clarification (M1.4, `_clarify.py`)

At most one question per turn; the first trigger wins:
1. the model asked (`draft.needs_clarification`) → `model_asked`;
2. a measure phrase the catalogue resolves to two or more metrics → `ambiguous_measure`, options = their labels;
3. a two-window shape (`period_comparison`) with no baseline and no prior turn → `missing_comparison_window`.

A named value recorded on several dimensions with comparable volume (`ambiguous_value`) and an
invalid spec are always asked, whatever the mode (A4: never guess).

## Failure policy

Invalid ids, carry failures, or ambiguous named values → `Clarification` naming
the problem. Never a silent partial spec.

## Tests

```bash
uv run pytest tests/unit/spec/ -q
```

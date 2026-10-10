# `understand`

## Purpose

One structured LLM call turns the user's question into a `QuestionDraft` (phrases and structured
window expressions; no dates, no required ids), or a `Clarification`.

## Interface

```python
from seleric_swarm.understand import understand, DraftLLM
from seleric_swarm.contracts.pipeline import QuestionDraft, Clarification   # the stage-boundary types
from seleric_swarm.adapters.understand_llm import PydanticAiDraftLLM          # the model call (L3)

out, stats = await understand(PydanticAiDraftLLM(model), question, read_model=read_model,
                              value_words=..., value_meanings=..., prior_question=..., prior_offer=...,
                              exemplars=...)
```

- `understand(llm, question, *, read_model, ...) -> (QuestionDraft | Clarification, stats)` — never raises.
- `DraftLLM` — the injected model call (`async (instructions, prompt) -> (draft_or_clarification, usage)`).
  This package imports no LLM SDK (A1/A2); tests pass a scripted `DraftLLM`.
- The prompt is built from the semantic read model only (labels, dimensions, hierarchies, value words).

Sub-package: `seleric_swarm.understand.examples` (M1.3).

## Flag

`UNDERSTAND_V2_MODE` (`off` | `shadow` | `enforce`).

## Failure policy (A4)

One retry, then `Clarification(reason="understanding_unavailable")`. Never fall back to treating
every value word as a filter.

## Tests

```bash
uv run pytest tests/unit/understand -q
```

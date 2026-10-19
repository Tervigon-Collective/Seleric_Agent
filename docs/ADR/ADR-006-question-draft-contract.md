# ADR-006 - QuestionDraft is a pipeline contract (contracts-v1.1)

## Status

Accepted (2026-10-11). Additive; no `contracts-v1` model changed. For T2 to review.

## Context

`QuestionDraft` is the hand-off from `understand` (M1.1) to `spec` (M1.2), but Part B of the plan did
not freeze it. Two tracks each wrote one: `understand/draft.py` (free-text windows and ranking) and
`understand/_draft.py` (structured `WindowSlot`/`WindowExpr`, `MeasurePhrase`, `rank_by`/`top_n`).
`spec` was written against the second and `understand()` returned the first, so `import
seleric_swarm.spec` failed (`WindowEnding`), the unit suite aborted at collection, and `spec` importing
`understand` broke the layers contract (same layer).

## Decision

- The structured draft (`QuestionDraft`, `WindowSlot`, `WindowExpr`, `WindowEnding`, `MeasurePhrase`,
  `ForecastSlots`, `ForecastHorizonSlot`) moves to `seleric_swarm.contracts.pipeline` (`_draft.py`).
  The free-text version is deleted: parsing "last 7 days" or "top 5 by X" in `spec` would be intent regex (A2).
- `understand` produces it and `spec` consumes it; neither imports the other.
- `understand` takes the model call as an injected `DraftLLM`; the pydantic-ai implementation is
  `seleric_swarm.adapters.understand_llm.PydanticAiDraftLLM`, so `understand` imports no LLM SDK.
- `RequiredScope`, `RequiredWindow` and `ValueFilter` move to `agent/scope_model.py` (no regex) and are
  re-exported from `agent/scope.py`, so `spec` can build a scope without importing that module's regex.

## Consequence

`QuestionDraft` is frozen like the rest of `contracts-v1`: a breaking change needs an ADR and a new
`schema_version`. Plan Part B lists it.

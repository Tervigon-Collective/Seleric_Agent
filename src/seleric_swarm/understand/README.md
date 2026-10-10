# `understand`

## Purpose

Structured reading of the user question into a `QuestionDraft` (phrases and
window expressions). The LLM call is M1.1 (T1); the draft type is shared with
M1.2 (spec compile).

## Interface

```python
from seleric_swarm.understand import QuestionDraft, understand
```

- `QuestionDraft` — pydantic output model
- `understand(...)` — M1.1 entry point (stub returns `Clarification` until T1 lands)

Sub-package: `seleric_swarm.understand.examples` (M1.3).

## Flag

`UNDERSTAND_V2_MODE` (`off` | `shadow` | `enforce`) — owned by M1.1 / T1.

## Failure policy

One retry, then `Clarification(reason="understanding_unavailable")`. Never fall
back to treating every value word as a filter.

# `answer`

## Purpose
Answers by construction (Phase 3). The model writes an `AnswerDraft` (claims citing refs, paragraphs with `{claim_id}` placeholders, table/chart blocks); code renders every number; the verifier checks structure.

## Interface
- `Evidence(items)`: the mission's `ResultSet`s and `Derivation`s; `resolve(ref, row_key)`.
- `render(draft, evidence, read_model) -> AnswerDocument` (M3.1): numbers from unit/aggregation/`extra.display`; tables from rows.
- `verify(draft, spec, evidence) -> Verdict` (M3.2): claims resolve; role↔op (`change`→delta/pct_change, `share`→share, `total`→sum or unsliced row); spec coverage (measures, window roles, breakdowns, entities); `not_in` exclusions carried; no digits outside placeholders (char scan); status/limitations consistent.
- `answer(llm, question, spec=, evidence=, read_model=)`: draft → verify → one revision → render. LLM injected (`AnswerLLM`; pydantic-ai in `adapters/answer_llm.py`).
- `turn_summary(spec, draft, evidence, read_model) -> TurnSummary` (M3.3): spec, offer, windows, labels, entities, top items.

## Flag
`ANSWER_V2_MODE` (`off|shadow|enforce`, default `off`). Wiring belongs in `pipeline/` and needs the v2 executor to produce evidence.

## Failure policy (A4)
No LLM / two failed calls → `failed` draft. Structural failure after one revision → ship `partial` with `unverified:<code>` limitations.

## Tests
`uv run pytest tests/unit/answer`

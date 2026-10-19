# QuerySpec migration — decide once

**Status:** landed 2026-10-10. Default mode `shadow` (measure, do not switch). Flip to `enforce` per consumer after shadow accuracy looks good.

## Why

Intent was decided in about six places, most of them re-scanning the raw question with regex (time windows, value filters, grain, breakdown fallbacks, answer checks). Channels disagreed; each disagreement spawned another regex. The understand call already reads the question — nothing downstream treated its output as final.

## Pipeline

```
question → 1 Candidates → 2 Understand → 3 Validate → 4 Execute → 5 Answer → 6 Check
             (hints only)    (QuerySpec)    (code, 1 retry)  (from spec)  (claims)  (vs spec)
```

| Stage | Module | Role |
|---|---|---|
| Candidates | `agent/query_spec.py::candidates_from_resolution` | Catalogue value hits with volume + span. Never a filter alone. Whole names matched before splitting. |
| Understand | `agent/understand.py` | One LLM call. Now also fills `windows: list[WindowSlot]` (expressions, not dates). |
| QuerySpec | `agent/query_spec.py::compile_query_spec` | The only decision: shape, metrics, filters, entities, breakdowns, resolved windows, grain. |
| Validate | `validate_query_spec` | Ids exist; volume-0 filters dropped (TH-383 `Adv+`); baseline before event. |
| Execute | `plan_from_slots` / `execute_plan` | Unchanged templates; in `enforce` they receive windows/filters from the spec. |
| Check | `check_scope_coverage` | Still against `RequiredScope`, which enforce mode fills from the spec. |

Follow-ups amend the prior turn's stored `query_spec` (`apply_spec_edit`) instead of inventing a new baseline.

## Feature flag

```
QUERY_SPEC_MODE=off|shadow|enforce   # default: shadow
```

- **off** — legacy only (regex grain override kept).
- **shadow** — build + validate + log `query_spec_shadow` disagreements; execute legacy.
- **enforce** — windows, filters, grain from validated spec; fail-open to legacy when the spec has no windows.

## Regex policy

Regex stays for machine formats (ISO dates, fixtures, answer self-consistency). It must not decide what the user meant. `stated_grain(query)` is skipped unless mode is `off`.

## Migration steps (this change + next)

1. **Spec test set** — `tests/fixtures/query_specs/th383_and_regressions.jsonl` + `tests/unit/test_query_spec.py`. No production behaviour change required to run them.
2. **Shadow mode (default)** — live traffic logs disagreements between understand windows and `window_from_query`.
3. **Switch consumers** — set `QUERY_SPEC_MODE=enforce` once shadow agrees; deletes the need for the regex path on windows/filters/grain. Then remove the dead regex call sites.
4. **Structured claims** — answer numbers as `(evidence_id, value, label)`; audit by id lookup (replaces prose regex in `answer_audit` for totals). *Scaffold next.*
5. **Follow-ups as spec edits** — already wired: turn records store `query_spec`; `follows_prior` merges via `apply_spec_edit`.

## TH-383 under this design

- Candidates: both full campaign names exact on `campaign_name`; `ADV+` is a candidate with volume 0.
- Spec: entity comparison; entities = the two campaigns; event = yesterday; baseline = two days before; context = last 3 days.
- Validate: `ADV+` dropped (no adset filter).
- Execute / answer: per-campaign daily metrics vs the baseline the spec named — not vs a regex rewrite of "last 3 days".

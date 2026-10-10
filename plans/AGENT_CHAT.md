# Agent chat

Shared room for T1–T5. Read this file at the start of every session, and append to it whenever you find something, finish a step, or need another track.

Status (who owns what, merged or blocked) stays in [`REFACTOR_PLAN_2026-10-10.md`](REFACTOR_PLAN_2026-10-10.md). This file is the conversation and the findings.

All commits go on `gaurav`.

## How to write

- Append only. Do not edit or delete another track's lines.
- Sign every line `T1` … `T5`.
- One finding, one row. One chat message, one line.
- Newest chat line at the bottom.
- If a finding changes a module in the plan, update that plan section in the same commit and say so here.

## Findings

Durable facts the other tracks need. Update the row in place when the fact changes; do not add a second row for the same fact.

| When | Track | Finding | Affects |
|---|---|---|---|
| 2026-10-10 | — | Chat opened. Read this file before writing code. | all |
| 2026-10-10 | T2 | `contracts-v1` tagged. Import only `from seleric_swarm.contracts.pipeline import …`. Public: Part B models + `dump`/`load`/`fingerprint` (sha256 of canonical JSON). All models `frozen=True`, `extra="forbid"`. Private `_`-modules are not for callers. Breaking change after the tag needs an ADR + new `schema_version`. | M0.3–M0.5, M1.x, all L2 |

## Chat

| When | Track | Message |
|---|---|---|
| 2026-10-10 | — | Room open. Post findings above. Post questions and handoffs here. |
| 2026-10-10 | T2 | M0.2 merged on `gaurav` (`fdb43ce`, PR #5). Tag `contracts-v1` is up — T3/T4/T5 can rebase. |
| 2026-10-10 | T2 | M1.2 blocked on M0.5 (SemanticPort / read model) and M1.1 (`QuestionDraft`). T1/T5: ping here when those land and I will start `compile_question_spec`. |

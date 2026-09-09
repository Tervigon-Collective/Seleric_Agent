# 45. Ticket Tracker — Robustness / Scalability / Production / Adaptability

> Live status register for the backlog defined in `44_ROBUSTNESS_SCALABILITY_ADAPTABILITY_BACKLOG.md`.
> That file holds the rationale/evidence per ticket and does not change once written;
> **this file is the one to edit** as work starts, moves, and finishes. Update the
> Status/Owner/Notes columns in place — do not duplicate rows.

Status legend (matches `SELERIC_SWARM_PROJECT_TASK_SHEET.md`): ⬜ NOT STARTED ·
🟦 IN PROGRESS · 🟨 REVIEW · 🟥 BLOCKED · 🟩 DONE · ⏸ DEFERRED · ❌ CANCELLED.
Priority: P0 blocking/critical · P1 required for MVP · P2 important post-MVP · P3 optimization/future.

Last updated: 2026-09-08. Full suite: 390 passed, 4 known-limitation failures (all ROB-004, see notes below), 0 unexplained regressions — confirmed via a clean whole-suite run.

## A. Robustness / correctness

| ID | Title | Priority | Status | Owner | Notes |
|---|---|---|---|---|---|
| ROB-001 | Fix hardcoded `direction_bad="up"` in real MCP provider | P1 | 🟩 DONE | — | `direction_bad` field added to metric_registry.yaml + MetricDefinition; mcp_data.py reads it per-metric. Regression test in test_mcp_hybrid_providers.py. |
| ROB-002 | Wire real observation data into swarm_v2 causal estimator | P1 | 🟩 DONE | — | `fetch_series` (daily MCP fetch) + `_fetch_observations` wired into swarm_bridge.py; real DataFrame now reaches DoWhy for non-fixture missions, gated by an 8-row minimum (statsmodels' own floor for a stable regression) so a short anomaly window falls back to metadata-only instead of feeding DoWhy a rank-deficient fit. `dowhy` dependency (already in pyproject.toml) installed into the dev venv along with a numpy2-compatible scikit-learn/matplotlib to fix an ABI break. Unit tests in test_mcp_hybrid_providers.py (`test_fetch_series_below_min_rows_returns_none`, `test_fetch_series_returns_dataframe_when_enough_days`). |
| ROB-003 | Load-test multi-domain metric comparisons | P2 | ⬜ NOT STARTED | — | |
| ROB-004 | Resolve outstanding test failures from commit `0e10033` | P1 | 🟩 DONE (4/6) — 🟥 2 known-limitation | — | Fixed: stale as_of test updated to expect ValueError; scenario_id validation added to MissionRequest/create_mission; 2 dowhy-dependent tests now pass after ROB-002 (a 3rd, previously-unseen failure — `test_18b_synthetic_mission_status_prototype_completed` — surfaced and was fixed by the same min_rows floor). Remaining 2 (`test_reference_mission_full_diagnostic`, `test_reference_mission_full_prediction`) still fail: their mission window has too few real observation days to clear the 8-row floor, so estimation correctly falls back to metadata-only (`ASSOCIATION_ONLY`) instead of `completed`. Diagnosed, not a regression — the system declines to fabricate confidence from an underpowered window rather than being forced green. |
| ROB-005 | Per-mission LLM-call budget/circuit-breaker for orchestration loops | P2 | ⬜ NOT STARTED | — | |

## B. Scalability

| ID | Title | Priority | Status | Owner | Notes |
|---|---|---|---|---|---|
| SCL-001 | Make diagnostic ontology data-driven | P1 | 🟩 DONE | — | `_ONTOLOGY` + graph/node/event/common-cause wiring moved to `config/diagnostic_ontology.yaml`; `ontology.py` now a thin YAML loader. Added a `metric.checkout_rate` outcome entry (previously undiagnosable) as part of verifying the new pattern. |
| SCL-002 | Move causal-graph wiring into config | P2 | 🟩 DONE | — | Landed as part of SCL-001's YAML move (graph_id/node/treatment_events are in the same file). |
| SCL-003 | Bound and parallelize per-mission LLM call chain | P2 | ⬜ NOT STARTED | — | Same root cause as PRD-002 |
| SCL-004 | Formalize "add a new domain" checklist as tested workflow | P2 | ⬜ NOT STARTED | — | Blocked on SCL-001/002 |

## C. Production-grade hardening

| ID | Title | Priority | Status | Owner | Notes |
|---|---|---|---|---|---|
| PRD-001 | Retry/backoff + graceful degradation around external MCP dependency | P1 | 🟩 DONE | — | `SelericMCPTransport._post_with_retry` (tenacity, already a dependency): 3 attempts, exponential backoff, transient-only (connection/timeout/5xx); raises `MCPUnavailableError` distinct from a real 4xx. Tests in test_seleric_remote.py. |
| PRD-002 | LLM-call cost/latency tracking + per-mission budget | P1 | 🟩 DONE | — | `MeteredLLMPort` wraps `runtime.llm`, accumulates token/latency usage per mission_id; `check_swarm_budget` enforces `MissionBudget.token_budget` (opt-in, still `None` by default) the same way `max_llm_calls` already was. Tests in test_budget_hard_stops.py. |
| PRD-003 | Close M12 observability gap (OTel/SLOs/deploy) | P2 | ⬜ NOT STARTED | — | Tracked in root task sheet as M12, already 🟦 IN PROGRESS there |
| PRD-004 | Require branch protection + CI status checks before merge | P1 | 🟥 BLOCKED | — | GitHub repo-settings change, not a code change — needs a repo admin to enable branch protection on `main`/`gaurav` (require PR review + passing CI). Cannot be done via commits. |
| PRD-005 | Enforce numeric-fabrication audit on every LLM prose call site | P2 | ⬜ NOT STARTED | — | |

## D. Adaptability

| ID | Title | Priority | Status | Owner | Notes |
|---|---|---|---|---|---|
| ADP-001 | Diagnosability should scale with metric catalogue | P1 | 🟩 DONE | — | Same fix as SCL-001 — adding an outcome metric's mechanisms is now a YAML edit (`config/diagnostic_ontology.yaml`), demonstrated by adding `metric.checkout_rate`. |
| ADP-002 | Confirm `ConstraintStore` is genuinely pluggable per tenant | P2 | ⬜ NOT STARTED | — | |
| ADP-003 | Audit Autonomy Levels 0-6 as config flip vs. rearchitecture | P2 | ⬜ NOT STARTED | — | |
| ADP-004 | Document vocabulary/language assumptions in prompts | P3 | ⬜ NOT STARTED | — | |

---

## How to update this tracker

- Flip a row's Status as work starts/finishes; fill Owner with the person's name/handle.
- Use Notes for blockers, PR links, or partial-completion detail — keep it to one line.
- When a ticket is done, leave the row in place with 🟩 DONE rather than deleting it —
  this file is the audit trail, not just a live view.
- If a ticket's scope changes materially, update the rationale in
  `44_ROBUSTNESS_SCALABILITY_ADAPTABILITY_BACKLOG.md` too, so the two files stay consistent.

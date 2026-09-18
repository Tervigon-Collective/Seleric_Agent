# Task Sheet

Tracks execution of the plan "Remove heuristic query-resolution in favor of
direct, LLM-trusted tool calls" (`~/.claude/plans/nested-wiggling-brooks.md`,
started 2026-09-16). Status reflects the state at time of writing — check the
referenced files before assuming a "Done" entry is still done as described.

## Context

Bugs #8 and #14 (see `docs/BUG_SHEET.md`) both trace to the same root cause:
`Seleric_Agent`'s swarm_v2 query-resolution pipeline ran an LLM classification
call and then several hand-written heuristic correction passes (keyword
token-matching, alias scoring, word-list lookups) instead of trusting the LLM
— which already has the metric catalog in its prompt — to say what it means
directly. `Base_Agent`'s simpler tool-calling architecture doesn't have this
heuristic layer and doesn't hit this bug class. Goal: rebuild on that
principle while keeping Skeptic/DoWhy/remediation/budget governance intact.

## Done

### Phase 1 — Richer classifier context + direct-parameter output schema
- `services/metrics.py::catalog_prompt()` now lists real live-catalogue
  metrics (id, domain, description, aliases, supported dimensions) instead of
  just a category→domain map — previously the classifier prompt gave the LLM
  **no actual metric list** once the live catalogue was warm (production
  default).
- `SwarmClassificationV1` (`coordinator/intake/llm_classifier.py`) gained
  `dimensions: list[str]` (real catalogue dimension ids, direct from the LLM)
  and `granularity: Literal["day","week","month","none"]`.
- Prompt (`prompts/coordinator/classify_swarm.v1.yaml`) instructs the LLM to
  fill both directly, with a granularity rule specifically for "why did X
  change over the last N days" diagnostic phrasing.
- `NormalizedQuery.granularity` threads the field through
  `coordinator/intake/__init__.py::normalize_query`.
- Test: `tests/unit/test_swarm_llm_classifier.py::test_classify_query_via_llm_sets_day_granularity_for_multiday_diagnostic`.

### Phase 2 — Thin existence/support validation (swarm_v2 path only)
- `llm_classifier.py` now trusts `classification.metric_hints`/`dimensions`
  directly (validated against the live registry / the metric's real
  `supported_dimensions` via new `catalogue_grounding.validate_dimensions_for_metric`)
  instead of running `hints_from_catalogue` (catalogue-search-and-score) or
  `apply_catalogue_grain`/`constrain_hints_to_grain`/`ground_live_grain`
  (query-text re-derivation of grain).
- **Not done / blocked-by-design**: the full deletion list from the original
  plan (`dimensions_in_query`, `breakdown_from_query`, `pick_grain`,
  `query_has_grain_intent`, etc.) was **not** carried out. Found that
  `agents/coordinator.py` (legacy lookup_v1 classifier) still imports
  `apply_catalogue_grain`/`hints_from_catalogue` directly, and
  `orchestration/graph.py`/`runner.py` still exist and are still a live
  fallback route (`orchestration/dispatch.py::run_any_mission` calls
  `run_mission` whenever `run_lookup_fast_path` returns `None`). A prior
  session's summary claimed lookup_v1 was fully retired; that never actually
  landed in git. Deleting the shared heuristic functions now would break
  that fallback — left them defined, just unused by the swarm_v2 path.

### Removed the comparison+grain fallback gap (lookup_fast_path.py)
- This was the last named structural blocker in
  `docs/features/lookup-v1-retirement.md` to closing the legacy fallback's
  remaining scope. `run_lookup_fast_path` now answers a dimensioned
  breakdown across two periods directly (`coordinator/lookup_fast_path.py`),
  fetching each period via the same MCP breakdown path as a plain grained
  lookup and delta-matching rows by `(metric, dimensions)`. Doc updated to
  "Phase 2c (done)".
- Test: `tests/unit/test_lookup_fast_path.py::test_comparison_with_grain_computes_per_dimension_delta`.
- Remaining fallback trigger: `comparison_range` staying unresolved (a
  classification-confidence gap, not a missing capability).

### Phase 3 — One fetch path driven by the resolved plan
- Found that swarm_v2's real fetch path (`swarm/specialists/observer.py::ObserverAgent`)
  never used the heuristic grounding functions at all — it already just
  reads `mission.context["domain_questions"]`, already improved by Phase 1/2.
- Threaded `NormalizedQuery.granularity` into `mission.context["granularity"]`
  (`coordinator/graph.py`). `ObserverAgent.run()` now fetches one Evidence row
  per day (capped at 31 days, `_daily_windows` helper) when granularity is
  `"day"`, instead of one window-aggregate — the direct architectural fix for
  bug #14, in place of the sum/normalize band-aid in `anomaly.py`.
- Test: `tests/unit/test_domain_questions.py::test_observer_fetches_one_evidence_row_per_day_when_granularity_is_day`.

### Phase 4 — Anomaly detection consumes the real per-day series
- Verification only, no logic change needed: `anomaly.py`'s per-evidence loop
  already handles each Evidence row independently, so real per-day rows
  (start==end) pass through unnormalized automatically.
- Reworded the sum/normalize branch's comment to state it's now a defensive
  fallback (comparison missions, or diagnostic phrasing the classifier
  doesn't flag as per-day) rather than the primary path.
- Test (new, end-to-end): `tests/unit/test_anomaly_specialist.py::test_observer_per_day_evidence_reaches_anomaly_unnormalized`
  — chains `ObserverAgent` → `AnomalyAgent` on one blackboard, proves 5
  distinct daily values reach the detector rather than a divided average.

**Verification across all phases**: full suite (`tests/unit`, `tests/coordinator`,
`tests/contract`, `tests/replay`) — 534 passed, only 2 pre-existing unrelated
failures (`test_health_combo_never_returns_running`,
`test_18b_synthetic_mission_status_prototype_completed`) plus one confirmed
live-data flake (`test_characterize_multi_day_window_last_point_vs_period_total`,
passes in isolation, hits real MCP data, not on any changed code path).

## Not done yet

### Phase 5 — Replace the heuristic-specific tests
- `tests/unit/test_catalogue_grounding.py` still directly unit-tests
  `dimensions_in_query`/`apply_catalogue_grain`/etc. — these are unused by
  the swarm_v2 path now but still live (legacy path dependency, see Phase 2
  note above). Plan called for replacing these tests 1:1 against the new
  resolver; not started.
- ~~Live-query verification~~ **Done 2026-09-17**: started the local dev
  server (`scripts/run_dev.py`, real `azure_openai_compatible` LLM, not
  FakeLLM) and re-ran both exact production queries via `POST /v1/missions`:
  - Bug #8's query, `"why did sales drop from 5 days, get per day data"`
    (`MS-83c8033ee8`) — routed to `swarm`, produced 5 Evidence + 5 Anomaly
    artifacts for `metric.net_sales`, no `session_day_of_week` dimension
    misclassification.
  - Bug #14's phrasing, `"why did net sales drop over the last 5 days"`
    (mission id in `mission2.json` scratch output) — 10 Evidence + 10 Anomaly
    artifacts (2 metrics × 5 days), confirming one real per-day row per
    metric rather than a window-aggregate sum.
  - Both missions completed with `skeptic verdict: PASS`, and both correctly
    flagged a premise mismatch (query says "dropped", data says net sales
    rose +57.6% over the window) — the Skeptic's own independent check
    caught it, exactly the intended behavior.
  - **Not fully independently cross-checked via MCP**: `seleric-mcp`'s
    `metrics_query` (the numeric Cube-backed data path) returned
    `ConnectError: All connection attempts failed` on every attempt during
    this session — an external backend-connectivity gap in this environment,
    unrelated to any code in this repo (`catalogue_list_brands`, a metadata-only
    call on the same MCP server, succeeded fine). Numeric cross-check against
    the live cube is still open if that connectivity is available in a future
    session.

### Full lookup_v1 deletion (separate from this plan's goal)
- `orchestration/graph.py`/`runner.py` and `agents/coordinator.py` still
  exist. Per `docs/features/lookup-v1-retirement.md`, actual deletion was
  gated on budget-enforcement parity and initial-lead-selection parity.
- **Budget-enforcement parity: moot as of 2026-09-17** — `governance/budget.py`'s
  `check_budget`/`check_hard_stops`/`check_swarm_budget` were deliberately
  disabled system-wide (all now unconditionally return ok; see that module's
  docstring). Both `lookup_fast_path.py` and legacy `run_mission` now agree
  in not enforcing any LLM/tool/agent-call/runtime ceiling, so there is no
  remaining parity gap between them on this axis — not because parity was
  built, but because the thing being compared no longer exists on either
  side. (A brief preflight-guard fix was drafted for the fast path during
  this session, then abandoned once budget enforcement was disabled
  entirely — it would have called into permanently no-op checks.)
- **Initial-lead-selection parity: verified 2026-09-17**. The doc's "3 failing
  tests" note was stale — `tests/replay/test_leadership_transfer.py`/
  `test_domain_lookups.py` all pass (23/23, stable across repeated runs).
  Ran the same 8 multi-domain queries directly through
  `run_lookup_fast_path`: `initial_mission_lead` matched legacy's exactly in
  every case, and both requested metrics came back with correct values in
  every case. `mission_lead` stays equal to `initial_mission_lead` on the
  fast path (no handoff, by design, per BUG_SHEET.md design tradeoff #9) —
  an already-documented, intentional difference from legacy's post-handoff
  final lead, not a selection mismatch.
- **Both readiness gaps are now closed.** What's left before actual deletion
  is a judgment call, not a blocker: the classifier's `grain`-detection gap
  (Phase 2a of the retirement doc) means some "per channel"-style queries
  answer with an aggregate instead of a breakdown, and lookup_v1 currently
  masks that by being a fallback. Deleting lookup_v1 removes that mask.
  Whether that's acceptable, and whether to actually delete
  `orchestration/graph.py`/`runner.py`/`agents/coordinator.py`, is a decision
  for the user — not attempted this session.

### docs/BUG_SHEET.md bug #14 entry
- Bug #14 (multi-day evidence sum vs single-day anomaly baseline) was fixed
  and tested in an earlier session but was never actually written up as an
  entry in `docs/BUG_SHEET.md` — still a documentation gap as of this
  writing.

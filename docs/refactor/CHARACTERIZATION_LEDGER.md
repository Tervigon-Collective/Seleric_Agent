# Characterization ledger (Profile B Sprint 2 → Sprint 3 gate)

Sprint 2 / Sprint 3 deletion of remaining MCP fetch wrappers requires the
semantic characterization suite to pass cleanly on **3 separate calendar
days**. This file is the dated ledger — do not mark the gate Done until
three dated rows exist below.

## How to append a day

```text
.venv/Scripts/python.exe -m pytest tests/replay/test_semantic_toolset_characterization.py -q
```

Paste the pytest summary line under a new `### YYYY-MM-DD` heading.

## Day 1 — 2026-09-18

- Prior session: multiple clean runs of
  `tests/replay/test_semantic_toolset_characterization.py` (3/3 cases) plus
  legacy `test_data_access_characterization.py` (7/7) when live MCP was up.
- Sprint 2 flow-fix session re-run: focused suite
  `test_causal_toolset` + `test_v3_validation` + `test_mcp_hybrid_providers` +
  `test_lookup_fast_path` + `test_provider_selection` +
  `test_semantic_toolset_characterization` → **52 passed** in 68.88s
  (2026-09-18).
- **Still only one calendar day** — days 2 and 3 required before deletion
  gate / catalogue heuristic retirement can claim Done.

## Day 2 — 2026-09-19

- Recorded during the cross-profile verification checkpoint (`SPRINT_PLAN.md`).
- Live run, both characterization suites together:
  `./.venv/Scripts/python.exe -m pytest -q tests/replay/test_semantic_toolset_characterization.py tests/replay/test_data_access_characterization.py`
  → **10 passed in 37.49s** (2026-09-19). The 37s wall-clock confirms it ran
  against live MCP rather than skipping — a skipped run returns in under a
  second, which is the thing to check before counting a day.
- Full suite the same day: 911 passed / 1 failed (pre-existing
  `test_health_combo_never_returns_running`) / 4 skipped.
- **Day 2 of 3.** One more separate calendar day is required before Sprint 3
  B's deletions (`catalogue_grounding.py` heuristics, `MetricRegistry`
  retirement) may claim Done.

## Day 3 — _(empty — append next calendar day)_

Reminder for whoever records Day 3: the gate is three *separate calendar
days*, so this cannot be closed by re-running today. Check the wall-clock on
the replay suite before counting the day — a fast pass means MCP was down and
the tests skipped, which does not count.

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

## Day 2 — _(empty — append next calendar day)_

## Day 3 — _(empty — append next calendar day)_

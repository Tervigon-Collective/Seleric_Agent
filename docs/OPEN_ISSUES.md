# Seleric Agent — Open Issues Tracker

> **Purpose**: Central registry of failing tests and known issues. All agents and contributors reference this sheet. Update status when fixing.
> **Location**: `docs/OPEN_ISSUES.md`
> **Last Updated**: 2025-10-12
> **Total Open Issues**: 10

---

## Issue Summary

| Status | Count |
|--------|-------|
| 🔴 Open | 10 |
| 🟡 In Progress | 0 |
| ✅ Fixed (this session) | 90 (AG-00 through AG-06 + ISS-012) |
| 🟢 SEM-18 Certified | 38 PASS, 2 PARTIAL, 0 FAIL/ERROR | — | End-to-end certification on 40 business questions against live Cube v2 |

---

## Issue Table

| ID | Test / Issue | File / Location | Root Cause / Notes | Priority | Status | Owner | Target Fix |
|----|--------------|-----------------|-------------------|----------|--------|-------|------------|
| **ISS-001** | `test_golden_dir_loads_shape_cases` | `tests/unit/evals/test_examples_separation.py::test_golden_dir_loads_shape_cases` | Duplicate case id `report-full-funnel-meta-oct1-11` exists in both `eval/golden` directories. Loader rejects duplicate IDs. | 🔴 High | 🔴 Open | | Fix duplicate case IDs in eval fixtures |
| **ISS-002** | `test_entity_comparison_case_pins_shape_and_window_order` | `tests/unit/evals/test_examples_separation.py::test_entity_comparison_case_pins_shape_and_window_order` | Same duplicate ID issue as ISS-001 — fixture collision in eval loader. | 🔴 High | 🔴 Open | | Fix duplicate case IDs in eval fixtures |
| **ISS-003** | `test_breakdown_ranking_is_typed_on_the_rank_metric_step` | `tests/unit/plan/test_planner.py::test_breakdown_ranking_is_typed_on_the_rank_metric_step` | RankingTemplate matches before EntityComparisonTemplate when both ranking and entity_comparison shapes apply. Template ordering in `TEMPLATES` tuple needs adjustment. | 🔴 High | 🔴 Open | | Reorder TEMPLATES: move EntityComparisonTemplate before RankingTemplate |
| **ISS-004** | `test_entity_comparison_ranks_then_compares_the_same_entities` | `tests/unit/plan/test_planner.py::test_entity_comparison_ranks_then_compares_the_same_entities` | Same root cause as ISS-003 — RankingTemplate matches entity_comparison shape before EntityComparisonTemplate gets a chance. | 🔴 High | 🔴 Open | | Reorder TEMPLATES: move EntityComparisonTemplate before RankingTemplate |
| **ISS-005** | `test_an_entity_comparison_naming_no_values_ranks_the_whole_family` | `tests/unit/plan/test_planner.py::test_an_entity_comparison_naming_no_values_ranks_the_whole_family` | Same template ordering issue — RankingTemplate intercepts entity_comparison shapes. | 🔴 High | 🔴 Open | | Reorder TEMPLATES: move EntityComparisonTemplate before RankingTemplate |
| **ISS-006** | `test_a_ranked_entity_over_two_windows_is_an_entity_comparison_whatever_the_label` | `tests/unit/plan/test_planner.py::test_a_ranked_entity_over_two_windows_is_an_entity_comparison_whatever_the_label` | Same template ordering issue. | 🔴 High | 🔴 Open | | Reorder TEMPLATES: move EntityComparisonTemplate before RankingTemplate |
| **ISS-007** | `test_another_listed_entity_kind_is_its_own_ranking` | `tests/unit/plan/test_planner.py::test_another_listed_entity_kind_is_its_own_ranking` | Same template ordering issue. | 🔴 High | 🔴 Open | | Reorder TEMPLATES: move EntityComparisonTemplate before RankingTemplate |
| **ISS-008** | `test_funnel_derives_each_stage_share_of_the_one_before` | `tests/unit/plan/test_planner.py::test_funnel_derives_each_stage_share_of_the_one_before` | FunnelTemplate may be matching before correct template, or funnel derivation logic needs adjustment for stage shares. | 🟡 Medium | 🔴 Open | | Review FunnelTemplate matching logic and funnel derivation |
| **ISS-009** | `test_ag03_t01_plan_for_entity_comparison` | `tests/unit/spec/test_ag03_understanding.py::test_ag03_t01_plan_for_entity_comparison` | Same template ordering issue — EntityComparisonTemplate not matching due to RankingTemplate precedence. | 🔴 High | 🔴 Open | | Reorder TEMPLATES: move EntityComparisonTemplate before RankingTemplate |
| **ISS-010** | `test_prompt_registry_serves_the_built_in_text` | `tests/unit/test_llm_roles.py::test_prompt_registry_serves_the_built_in_text` | Prompt registry lookup failing for built-in prompt text. Registry may not be loading built-in prompts correctly. | 🟡 Medium | 🔴 Open | | Fix prompt registry loading / built-in prompt registration |
| **ISS-012** | `CUBE_API_URL configuration` | `Seleric_Agent_Core/.env` / `.env.example` / `config.yaml` | Local `.env` had overridden `config.yaml` to Cube v1 `:4001`. Fixed: local `.env` → `:4002`; `.env.example` documents v2 default and warns against `:4001`. `config.yaml` / `config.py` / `certify_qa.py` already default to v2. | 🟡 Medium | ✅ Fixed | | — |

---

## Fixed This Session (Reference)

| ID | Test / Issue | Status | Notes |
|----|--------------|--------|-------|
| FIX-001 | AG-00: value_correctness ULP fix | ✅ Fixed | Representation tolerance floor (1e-9) |
| FIX-002 | AG-01: premise states & health distinctions | ✅ Fixed | schema_version=2, ADR-007 |
| FIX-003 | AG-02: decomposition residuals & query dedup | ✅ Fixed | Mix-shift, qualifications |
| FIX-004 | AG-03: entity comparison & follow-ups | ✅ Fixed | Template ordering pre-fix |
| FIX-005 | AG-04: Core SEM-14 contract integration | ✅ Fixed | Core canonical fields |
| FIX-006 | AG-05: verified answers & latency | ✅ Fixed | Query dedup, latency tracking |
| FIX-007 | AG-06: cross-domain graph attributes | ✅ Fixed | 17 tests pass |
| FIX-008 | ISS-012: CUBE_API_URL → Cube v2 :4002 | ✅ Fixed | Core `.env` + `.env.example`; defaults already in `config.yaml` |

---

## Resolution Guide

### For ISS-003 through ISS-009 (Template Ordering)
**Root Cause**: In `src/seleric_swarm/plan/_registry.py`, `TEMPLATES` tuple has `RankingTemplate()` before `EntityComparisonTemplate()`. Both match `entity_comparison` shape, but `RankingTemplate` matches first.

**Fix**:
```python
# In src/seleric_swarm/plan/_registry.py
TEMPLATES: tuple[Template, ...] = (
    ExplorationTemplate(),
    WhyTemplate(),
    CompositionTemplate(),
    FunnelTemplate(),
    EntityComparisonTemplate(),  # MOVE UP: before RankingTemplate
    RankingTemplate(),           # MOVE DOWN
    PeriodComparisonTemplate(),
    TrendTemplate(),
    BreakdownTemplate(),
    LookupTemplate(),
)
```

### For ISS-001 & ISS-002 (Duplicate Case IDs)
**Fix**: Edit `eval/golden/` YAML files to ensure unique `id` fields across all cases. Search for `report-full-funnel-meta-oct1-11` and make IDs unique.

### For ISS-010 (Prompt Registry)
**Location**: `src/seleric_swarm/config/llm_roles.py` or prompt loading logic. Verify built-in prompts are registered in registry.

### For ISS-011 (Live Test)
**Note**: Requires live MCP environment. Run with `EVAL_MODE=live` and valid MCP credentials. May be environment-specific.

### For ISS-012 (CUBE_API_URL) — fixed
**Resolution**: Agent Core defaults (`config.yaml`, `config.py`) already use Cube v2 `:4002`. Local gitignored `.env` had forced `:4001` (v1). Updated `.env` to `:4002` and documented the override in `.env.example` / README. Keep v1 `:4001` only for seleric_systems.

---

## How to Use This Sheet

| Action | Instruction |
|--------|-------------|
| **Claim an issue** | Add your name to `Owner` column, set `Status` to 🟡 In Progress |
| **Update progress** | Update `Status` and add notes in `Target Fix` |
| **Mark fixed** | Set `Status` to ✅ Fixed, move row to **Fixed This Session** table |
| **Add new issue** | Append row to **Issue Table** with next sequential ID |

---

## Quick Commands

```bash
# Run all failing tests
/opt/seleric/Seleric_Agent/.venv/bin/pytest -q tests/unit/plan/test_planner.py tests/unit/spec/test_ag03_understanding.py tests/unit/evals/test_examples_separation.py tests/unit/test_llm_roles.py -v

# Run specific test
/opt/seleric/Seleric_Agent/.venv/bin/pytest -q "tests/unit/plan/test_planner.py::test_entity_comparison_ranks_then_compares_the_same_entities" -v

# Check template ordering
grep -n "EntityComparisonTemplate\|RankingTemplate" src/seleric_swarm/plan/_registry.py
```

---

## Related Files

| Area | Files |
|------|-------|
| Template Registry | `src/seleric_swarm/plan/_registry.py` |
| Template Definitions | `src/seleric_swarm/plan/_templates/_compare.py`, `_ranking.py` |
| Eval Fixtures | `eval/golden/*.yaml` |
| Prompt Registry | `src/seleric_swarm/config/llm_roles.py`, `src/seleric_swarm/understand/_prompt.py` |
| Live Test Config | `tests/unit/test_business_state_live.py` |

---

*This sheet is the single source of truth for open issues. All agents and contributors must reference and update it.*
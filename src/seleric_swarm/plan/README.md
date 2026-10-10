# `plan`

## Purpose

Turn a validated `QuestionSpec` into a typed `AnalysisPlan` (M2.1) and run its query steps
(M2.2). Pure code: catalogue facts come only from the `SemanticReadModel`; no question text,
no `re`, no metric/dimension names (A2).

## Interface

```python
from seleric_swarm.plan import plan, plan_notes, template_ids, execute, AGENT_TEMPLATE_ID
```

- `plan(spec, read_model, *, as_of=None, headline_metric_ids=()) -> AnalysisPlan`
  - `as_of`: the mission day. A compared window that contains it counts additive metrics
    through the elapsed hours (`QueryStep.elapsed_only`), and a "why" is compared instead.
  - `headline_metric_ids`: the configured business headline, injected by the pipeline, used
    when a comparison names no measure.
- `plan_notes(...)`: the corrections the planner made (entity re-picked, volume added, …).
- `template_ids()`: the shapes the registry plans.
- `execute(steps, windows, port, read_model, as_of, ...)`: see `_execute.py` (M2.2).

## Templates (`_templates/`, ordered in `_registry.py`)

A template has `id`, `matches(prepared)` and `build(prepared, ctx)`. `prepare()` normalises
the spec once (ports `agent/plan.py::plan_from_slots` / `compose_plan`):

- unknown metric ids are dropped; ratio metrics read across slices get their catalogue
  `volume_metric` beside them;
- an entity the rank metric cannot carry is replaced by a breakdown it can;
- a ranked entity over two windows is an `entity_comparison`, whatever the label;
- a "why" whose window contains today becomes a `period_comparison`.

| id | steps |
|---|---|
| `exploration` (kind) | `EngineStep(engine="explore")` |
| `why` | `EngineStep(engine="investigate")`: outcome + named drivers |
| `composition` | `EngineStep(engine="composition")` |
| `funnel` | one query per stage, `share` of each stage in the one before |
| `entity_comparison` | `rank` step, one ranking per other entity kind, then each metric over both windows on the ranked entities + `delta`/`pct_change` |
| `period_comparison` | each metric over baseline and event + `delta`/`pct_change` (`per_day` when lengths differ); breakdowns on the event window |
| `trend` / `breakdown` / `lookup` | one query per metric on the event window, sliced by what it carries; typed `RankingSpec` on the rank metric |

## Flag

`PLANNER_V2_MODE` (`off` | `shadow` | `enforce`, default `off`). Wiring belongs in `pipeline/`.

## Failure policy (A4)

No template, or a template that cannot build → `AnalysisPlan(template_id="agent", needs_agent=True)`
with no steps: the agent loop answers with tools.

## Tests

`uv run pytest tests/unit/plan`

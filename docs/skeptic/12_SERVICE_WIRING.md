# 12 - Production Service Wiring

`agents/skeptic/services/` holds the adapters that replace the in-memory
defaults with real backends. All are **opt-in** via `SkepticDeps`; the Skeptic
stays deterministic and fully functional without them.

```python
from seleric_swarm.agents.skeptic import SkepticAgent, SkepticDeps
from seleric_swarm.agents.skeptic.services import (
    DoWhyCausalValidationService,
    model_registry_from_yaml,
    ConstraintStoreBusinessRuleService,
)
from seleric_swarm.agents.skeptic.services.business_rules import InMemoryConstraintStore, ConstraintSnapshot
from seleric_swarm.agents.skeptic.registries import causal_graphs_from_yaml

deps = SkepticDeps(
    model_registry=model_registry_from_yaml(),                 # config/model_registry.yaml
    causal_graphs=causal_graphs_from_yaml(),                   # config/causal_graphs.example.yaml
    causal_service=DoWhyCausalValidationService(causal_graphs_from_yaml()),
    rules=ConstraintStoreBusinessRuleService(InMemoryConstraintStore(ConstraintSnapshot(...))),
    drift_monitor=MyPSIDriftMonitor(),                         # implements registries.DriftMonitor
)
agent = SkepticAgent(evidence_repo=..., artifact_repo=..., deps=deps)
```

## 1. DoWhy causal validation (`services/dowhy_causal.py`)

`DoWhyCausalValidationService` implements `CausalValidationService`.

- Always runs the metadata audit (`BasicCausalValidationService`): temporal
  order, graph support, confounder coverage, estimator sanity.
- When `context["observations"]` is a `pandas.DataFrame`, it also **re-estimates**
  via `seleric_swarm.causal.dowhy_service.DoWhyService` — identify → estimate →
  run placebo-treatment / random-common-cause / data-subset refuters — and folds
  the fresh refutation counts into the confidence tier.
- **Sign-flip guard**: if the re-estimated effect has the opposite sign to the
  artifact's reported effect → `confidence = REJECTED`.
- DoWhy missing or estimation failure → `DoWhyUnavailable` is caught, the service
  degrades to the metadata audit and records the reason. **Never a fake pass.**

`DoWhyService` (in `seleric_swarm/causal/`) is import-lazy: no import cost, no
hard dependency at module load.

## 2. Model registry + drift (`services/model_registry.py`)

- `model_registry_from_yaml()` → `YamlModelRegistry` reads
  `config/model_registry.yaml` (falls back to `.example`), mapping each entry to
  a `ModelRecord` (status, target, `minimum_history_days`, `backtest_available`,
  `last_validated_at`).
- `DriftMonitor` (Protocol, in `registries.py`) is the seam for PSI / KS /
  Jensen-Shannon / calibration monitors. The `ModelValidator` calls
  `deps.drift_monitor.status_for(model_id, features=...)` **only when the
  forecast artifact carries no drift status** (or `"unknown"`); a monitor outage
  is a warning, an `"unknown"` result downgrades `MODEL_VALID → MODEL_DEGRADED`.
- `NullDriftMonitor` (default) returns `"unknown"` — honest, never a pass.

## 3. Business rules (`services/business_rules.py`)

`ConstraintStoreBusinessRuleService` implements `BusinessRuleService` over a
`ConstraintStore` (Protocol). `ConstraintStore.snapshot(mission_id, domains)`
returns a `ConstraintSnapshot` (stock cover, margin floor + current margin,
open-PO risk, change-freeze window, max budget delta %).

Rules emitted:

| rule_id | trigger | severity |
| --- | --- | --- |
| `inventory.no_scale_when_stock_critical` | scale-spend action + `stock_cover_days < critical` | blocking |
| `finance.margin_floor` | discount action + projected margin < floor | blocking |
| `finance.budget_delta_cap` | spend change % > `max_budget_delta_pct` | warning |
| `procurement.open_po_risk_high` | scale-spend + open-PO risk high | warning |
| `technical.change_freeze` | deploy/rollback action during a freeze | warning |

`InMemoryConstraintStore` takes a `ConstraintSnapshot` (or dict) directly for
tests / offline runs. Production implements `ConstraintStore` against the live
finance / inventory / procurement / APM systems.

## 4. Swarm bridge (`agents/skeptic/swarm_bridge.py`)

`SwarmSkepticSpecialist` has the two-axis swarm specialist interface
(`agent_id` / `produces` / `policy` / `run(blackboard, mission)`) but delegates
to the full `SkepticAgent`, then writes an equivalent `Skeptic` Blackboard
artifact (verdict, challenges → `problems`, follow-ups, `trust:`/`risk:` quality
flags) so the synthesizer, completion gate and existing tests are unchanged.

Enable it per run:

```python
await run_swarm_mission(runtime, query=..., full_skeptic=True)
```

`full_skeptic` defaults to `False`, so the lightweight in-loop specialist
remains the default until the swap is promoted.

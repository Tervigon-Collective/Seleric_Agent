# 02 - Claim Model & Contracts

All contracts live in `agents/skeptic/contracts.py` (Pydantic v2).

## ClaimType

```
numeric | comparison | anomaly | correlation | causal
       | forecast | recommendation | action | qualitative
```

Different types route to different validation pipelines
(`planning/validation_router.py`).

## Claim

| field | notes |
| --- | --- |
| `claim_id`, `mission_id` | ids |
| `claim_type` | one of the above |
| `statement` | the natural-language claim |
| `origin_agent` | who produced it |
| `support_refs` | union of all evidence + typed refs (parser mirrors typed lists here) |
| `contradiction_refs` | refs the origin agent already knows conflict |
| `metric_refs` / `anomaly_refs` / `causal_refs` / `diagnostic_refs` / `model_refs` / `forecast_refs` / `strategy_refs` | typed artifact refs |
| `metadata` | free dict: `impact`, `reversibility`, `financial_magnitude`, `diagnosed_mechanism`, `alternatives_to_test`, `competing_definitions`, ... |

`intake/claim_parser.parse_claim()` also accepts the repo's lean
`seleric_swarm.domain.models.Claim` dict (`text`, `causal_ref`, `model_ref`) and
normalizes it.

## Upstream artifact contracts

Each has a `from_blackboard(payload: dict)` adapter so a **live swarm run**
(`seleric_swarm.swarm.artifacts`) is validated with no upstream change, and a
future agent can construct the richer contract directly.

- `EvidenceArtifact` - value, baseline, time_range, timezone, dimensions,
  freshness, `query_hash`, `source_version`, `calculation_version`,
  `sample_size`, `quality_flags`.
- `AnomalyArtifact` - observed/expected, deviation, detector id+version,
  analysis window, sample size, history days, seasonality flag.
- `CausalAnalysisArtifact` - treatment/outcome, graph id+version, common causes,
  estimator + params, effect + CI, refutation_results, assumptions, limitations,
  `treatment_started_at`, `outcome_started_at`.
- `DiagnosticArtifact` - **future**; hypotheses, retained/rejected, methodology,
  limitations, `causal_ref`.
- `ForecastArtifact` / `PredictionArtifact` - **future**; target, prediction,
  interval, horizon, model id+version, feature set id+version, training window,
  backtest metrics, drift status, applicability status, `llm_generated`.
- `StrategyArtifact` - action, mechanism_ref, expected_effect, cost, risk,
  reversibility, owner_domain, constraints, prerequisites, measurement_plan.

## Output contracts

- `Challenge` - `category`, `severity` (info/warning/blocking), `description`,
  `evidence_refs`, `remediation_hint`, `detail`.
- `EvidenceGap` - `description`, `reason_required`, `capability_required`,
  `blocking`, `priority`.
- `FollowUpTask` - `requested_capability`, `objective`, `question`,
  `evidence_refs`, `priority`, `blocking`, `preferred_domain`. Stable
  `task_id` (`sha1(mission|claim|capability|question)`) so re-runs dedupe.
- `AlternativeHypothesis` - `hypothesis`, `mechanism`, supporting/contradictory
  observations, `falsification_test`, `priority`, `status`
  (open/supported/eliminated).
- `SkepticVerdict` - see `schemas/skeptic_verdict.schema.json`.

## Request

```python
class SkepticValidationRequest(BaseModel):
    mission_id: str
    claim: Claim
    evidence_refs: list[str] = []
    risk_context: dict[str, Any] = {}
    available_artifact_refs: list[str] = []
    blind_review: bool | None = None      # None -> auto by risk threshold
```

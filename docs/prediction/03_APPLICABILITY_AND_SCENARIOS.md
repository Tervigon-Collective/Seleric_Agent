# 03 - Applicability & Scenarios

## Applicability (`applicability.py`)

`assess_applicability` returns one of
`in_domain | near_domain | out_of_domain | regime_shift | unknown` plus notes.

Order of checks:

1. **Declared** - `request.context["applicability_status"]` in
   `applicability.reject_statuses` -> mapped straight through (`out_of_domain` /
   `regime_shift`).
2. **Regime shift** - the last step in history is large relative to historical
   per-point variation (> 6x the mean step) -> `regime_shift`.
3. **History sufficiency** - very short history (< `applic.min_history_days // 3`
   points) -> `near_domain`.
4. **Drift** - the produced forecast's `drift_status` (propagated into the
   context by `select_forecast`): reject statuses -> `out_of_domain`;
   amber/yellow -> `near_domain`.
5. else `in_domain` (or `unknown` with no history).

`out_of_domain` / `regime_shift` -> the confidence tier is forced to
`INSUFFICIENT_PREDICTIVE_EVIDENCE` and no claim is emitted (`test_drift_red_forces_insufficient`).

## Scenarios (`forecasting/scenarios.py`)

Built only when `scenarios.build` and the run has a 2-element interval. **No
number is invented** - each scenario is a function of the point + interval:

| scenario | value | assumption |
| --- | --- | --- |
| `base` | the point forecast | current trend continues at the modelled rate |
| `optimistic` | interval bound in the *good* direction (per metric polarity) | the driver resolves within the horizon |
| `pessimistic` | `point + (bad_bound - point) * cause_persistence` | the diagnosed cause persists |

`cause_persistence` = `scenarios.cause_persistence_high` (default 1.0) when a
referenced `CausalAnalysisArtifact` has `passed=True`, else
`cause_persistence_low` (default 0.4). So an unconfirmed cause pulls the
pessimistic case toward the point rather than the interval edge
(`test_scenarios_bounded_by_interval`).

Metric polarity: `metric.cac`, `metric.return_rate`, `metric.cpm`, `metric.cpc`,
`metric.js_error_rate`, `metric.mobile_lcp_seconds` are "up is bad"; everything
else is "down is bad".

"""Scenario projections from a ``ForecastRun``.

    base        = the point forecast
    optimistic  = interval bound in the 'good' direction
    pessimistic = interval bound in the 'bad' direction, scaled by how strongly
                  the diagnosed cause is expected to persist (cause_persistence)

No numbers are invented: every value is a function of the model/baseline point +
interval. If the run has no interval, no scenarios are built (a gap, not a guess).
"""

from __future__ import annotations

from seleric_swarm.agents.prediction.context import PredictionContext
from seleric_swarm.agents.prediction.contracts import ForecastRun, ScenarioProjection

# metrics where "up" is the bad direction
_BAD_IS_UP = {"metric.cac", "metric.return_rate", "metric.cpm", "metric.cpc", "metric.js_error_rate", "metric.mobile_lcp_seconds"}


def build_scenarios(ctx: PredictionContext, run: ForecastRun) -> list[ScenarioProjection]:
    if not ctx.policies.build_scenarios() or run.prediction is None or len(run.interval) != 2:
        return []

    lo, hi = sorted(run.interval)
    point = float(run.prediction)
    bad_is_up = ctx.target_metric in _BAD_IS_UP
    persist = ctx.policies.cause_persistence(ctx.cause_persistence)

    good_bound = lo if bad_is_up else hi
    bad_bound = hi if bad_is_up else lo
    # pessimistic scaled toward the point when the cause is not well supported
    bad_scaled = point + (bad_bound - point) * persist

    return [
        ScenarioProjection(name="base", prediction=round(point, 4), interval=[round(lo, 4), round(hi, 4)],
                           assumption="current trend continues at the modelled rate"),
        ScenarioProjection(name="optimistic", prediction=round(good_bound, 4), interval=[round(lo, 4), round(hi, 4)],
                           assumption="the driver resolves within the horizon"),
        ScenarioProjection(name="pessimistic", prediction=round(bad_scaled, 4), interval=[round(lo, 4), round(hi, 4)],
                           assumption=(
                               f"the diagnosed cause persists (persistence={persist:g}; "
                               f"{'causally supported' if ctx.causal_supported else 'not causally confirmed'})"
                           )),
    ]

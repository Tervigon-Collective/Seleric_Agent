"""Driver-conditioned level.

A target often moves with an observed driver that is available fresher than the
target itself (no maturity lag). The statistical path keeps its weekly shape, but
its *level* is re-anchored to what the driver's recent run-rate implies at the
target's recent efficiency (target per unit of driver). Metric-agnostic: the
driver ids come from the policy registry.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any

from seleric_swarm.forecasting.assembler import _fetch_many
from seleric_swarm.forecasting.types import DailyPoint, FeatureFrame

EFFICIENCY_DAYS = 42
RUNRATE_DAYS = 14
FACTOR_BOUNDS = (0.25, 4.0)


def apply_driver_level(
    days: list[DailyPoint],
    *,
    target: list[float | None],
    frame_dates: list[date],
    driver: dict[str, float],
    as_of: date,
) -> tuple[list[DailyPoint], dict[str, Any]] | None:
    """Rescale ``days`` to the driver-implied level. None when it cannot be done."""
    pairs = [
        (v, driver.get(d.isoformat()))
        for d, v in zip(frame_dates, target, strict=False)
        if v is not None and driver.get(d.isoformat()) is not None
    ][-EFFICIENCY_DAYS:]
    t_sum = sum(v for v, _ in pairs)
    d_sum = sum(x for _, x in pairs)
    if len(pairs) < 14 or d_sum <= 0 or t_sum <= 0:
        return None
    efficiency = t_sum / d_sum
    recent = [
        driver[k]
        for k in (
            (as_of - timedelta(days=i + 1)).isoformat() for i in range(RUNRATE_DAYS)
        )
        if k in driver
    ]
    if len(recent) < 7:
        return None
    run_rate = statistics.mean(recent)
    implied = run_rate * efficiency
    base = statistics.mean(d.p50 for d in days)
    if base <= 0 or implied <= 0:
        return None
    lo, hi = FACTOR_BOUNDS
    factor = min(max(implied / base, lo), hi)
    out = [
        DailyPoint(
            date=d.date,
            mean=d.mean * factor,
            p10=d.p10 * factor,
            p50=d.p50 * factor,
            p90=d.p90 * factor,
        )
        for d in days
    ]
    return out, {
        "driver_run_rate": run_rate,
        "efficiency": efficiency,
        "implied_daily_mean": implied,
        "statistical_daily_mean": base,
        "level_factor": factor,
        "efficiency_days": len(pairs),
        "run_rate_days": len(recent),
    }


async def fetch_candidates(
    mcp_client: Any, metric_ids: list[str], *, frame: FeatureFrame, as_of: date
) -> dict[str, dict[str, float]]:
    start = frame.context_start
    got = await _fetch_many(
        mcp_client,
        metric_ids=metric_ids,
        start=start,
        end=as_of - timedelta(days=1),
        agent_id="v3_agent",
        dimensions=None,
        filters=None,
    )
    return {m: v[0] for m, v in got.items() if v and v[0]}

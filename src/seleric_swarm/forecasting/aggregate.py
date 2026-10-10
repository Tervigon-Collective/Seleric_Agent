"""Week/month roll-ups from daily forecast points."""

from __future__ import annotations

from seleric_swarm.forecasting.derive import aggregate_days
from seleric_swarm.forecasting.types import DailyPoint


def rollup(days: list[DailyPoint], *, grain: str) -> list[DailyPoint]:
    """Aggregate daily points to week or month grain."""
    if grain not in {"week", "month"}:
        return list(days)
    return aggregate_days(days, grain=grain)

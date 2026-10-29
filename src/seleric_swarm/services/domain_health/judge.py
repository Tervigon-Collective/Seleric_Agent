"""Decide whether a metric's latest daily value needs attention.

Pure functions over a daily series, no I/O. They replace three defects of the first ready store:

* a percent change on a signed or negative base (net profit read "-448%"): change is reported in the unit that
  fits the metric's *shape* (percent for additive metrics, percentage points for ratios, absolute for signed ones);
* an anomaly in the *good* direction raised like a bad one: severity needs the move to go the metric's bad way, and
  a metric whose catalogue entry has no ``direction_bad`` can only be watched, never alerted;
* a band that ignores weekday seasonality and dips below zero for metrics that cannot be negative.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

Shape = Literal["additive", "ratio", "signed"]
Status = Literal["alert", "watch", "good", "ok", "no_data"]

MAD_TO_SIGMA = 1.4826
MIN_HISTORY = 10          # prior daily points needed before a baseline is trusted
MIN_WEEKDAY_POINTS = 3    # same-weekday points needed to use a weekday baseline
SIGMA_FLOOR_REL = 0.05    # a flat history must not make a 1% wobble look like an outlier
Z_THRESHOLD = 3.0
COUNT_SIGMA_FLOOR = 0.5   # with z=3 a count must move by 2 or more to leave its band


@dataclass
class Judgement:
    status: Status
    shape: Shape
    basis: str                       # "weekday" | "trailing" | "none"
    observed: float | None = None
    expected: float | None = None
    low: float | None = None
    high: float | None = None
    z: float | None = None
    direction: str | None = None     # which way observed sits against expected
    is_anomaly: bool = False
    worse_by: float | None = None    # positive = moved the bad way, in the unit of ``unit``
    unit: str | None = None          # "%" | "pp" | "abs"
    delta_pct: float | None = None   # only for additive metrics with a positive expectation
    reason: str = ""
    history_points: int = 0
    observed_on: str | None = None   # the day ``observed`` belongs to

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def infer_shape(catalogue_unit: str | None, values: list[float]) -> Shape:
    """Ratio comes from the catalogue unit; signed from the data itself (a metric that has gone negative)."""
    if (catalogue_unit or "").lower() == "ratio":
        return "ratio"
    if values and min(values) < 0:
        return "signed"
    return "additive"


def _day(ts: str) -> date | None:
    try:
        return date.fromisoformat(str(ts)[:10])
    except ValueError:
        return None


def _mad_sigma(values: list[float]) -> float:
    median = statistics.median(values)
    return MAD_TO_SIGMA * statistics.median([abs(v - median) for v in values])


def _baseline(history: list[tuple[date | None, float]], obs_day: date | None) -> tuple[float, float, str]:
    """(expected, sigma, basis). Same-weekday median when there are enough weekday points, else the trailing median.

    Sigma comes from each point's distance to its own weekday's median, so a normal Monday/Sunday gap does not widen
    the band the way a plain MAD over the whole window does.
    """
    values = [v for _, v in history]
    weekday = obs_day.weekday() if obs_day else None
    same = [v for d, v in history if d is not None and weekday is not None and d.weekday() == weekday]
    if len(same) >= MIN_WEEKDAY_POINTS:
        by_weekday: dict[int, list[float]] = {}
        for d, v in history:
            if d is not None:
                by_weekday.setdefault(d.weekday(), []).append(v)
        residuals = [
            v - statistics.median(by_weekday[d.weekday()])
            for d, v in history
            if d is not None and len(by_weekday[d.weekday()]) >= 2
        ]
        sigma = MAD_TO_SIGMA * statistics.median([abs(r) for r in residuals]) if residuals else _mad_sigma(values)
        return statistics.median(same), sigma, "weekday"
    return statistics.median(values), _mad_sigma(values), "trailing"


def judge(
    series: list[tuple[str, float]],
    *,
    unit: str | None,
    direction_bad: str | None,
    rule: dict[str, Any] | None = None,
    z_threshold: float = Z_THRESHOLD,
    expect_day: str | None = None,
) -> Judgement:
    """Judge the last point of ``series`` (ascending ``(iso_date, value)``) against its own history.

    ``rule`` is the user's per-metric choice: ``{"on": "anomaly"}`` (default) alerts when the value leaves its normal
    range the bad way; ``{"on": "worse_by", "threshold": n}`` alerts when it is worse than expected by at least ``n``
    (percent for additive metrics, percentage points for ratios, the metric's own unit for signed ones).
    """
    rule = rule or {}
    values = [v for _, v in series]
    shape = infer_shape(unit, values)
    last_day = str(series[-1][0])[:10] if series else None
    # The series drops days the source returned no row for. Its last point is then an older day, and judging it as
    # "yesterday" would alert on (or reassure with) a value that is not yesterday's. Say so instead.
    if expect_day and last_day != str(expect_day)[:10]:
        return Judgement(
            status="no_data", shape=shape, basis="none", observed=values[-1] if values else None, observed_on=last_day,
            history_points=max(len(values) - 1, 0),
            reason=f"no value for {str(expect_day)[:10]}; the latest is from {last_day}" if last_day else f"no value for {str(expect_day)[:10]}",
        )
    if len(values) < MIN_HISTORY + 1:
        return Judgement(
            status="no_data", shape=shape, basis="none", history_points=max(len(values) - 1, 0),
            observed=values[-1] if values else None, observed_on=last_day,
            reason=f"needs {MIN_HISTORY} prior days, has {max(len(values) - 1, 0)}",
        )
    observed = values[-1]
    history = [(_day(ts), v) for ts, v in series[:-1]][-27:]
    expected, sigma, basis = _baseline(history, _day(series[-1][0]))
    sigma = max(sigma, SIGMA_FLOOR_REL * abs(expected), 1e-9)
    if (unit or "").lower() == "count":
        sigma = max(sigma, COUNT_SIGMA_FLOOR)  # whole events: one stray cancel on a zero history is not an incident
    low, high = expected - z_threshold * sigma, expected + z_threshold * sigma
    if shape != "signed":
        low = max(low, 0.0)  # a count, amount or rate cannot be below zero
    z = (observed - expected) / sigma
    direction = "up" if observed > expected else "down" if observed < expected else "flat"
    is_anomaly = abs(z) >= z_threshold

    if shape == "ratio":
        move, unit_out = (observed - expected) * 100, "pp"
    elif shape == "signed":
        move, unit_out = observed - expected, "abs"
    else:
        move, unit_out = ((observed - expected) / expected * 100 if expected > 0 else 0.0), "%"
    sign = 1 if direction_bad == "up" else -1 if direction_bad == "down" else 0
    worse_by = sign * move if sign else None
    delta_pct = (observed - expected) / expected * 100 if shape == "additive" and expected > 0 else None

    if sign == 0:
        status: Status = "watch" if is_anomaly else "ok"
        reason = "outside its normal range" if is_anomaly else "within its normal range"
        if is_anomaly:
            reason += "; catalogue has no direction_bad, so this is not alerted"
    else:
        favourable = direction != "flat" and direction != direction_bad
        if rule.get("on") == "worse_by":
            threshold = float(rule.get("threshold", 0))
            hit = worse_by is not None and worse_by >= threshold
            reason = f"{worse_by:+.1f}{unit_out} worse than expected (alert at {threshold:g}{unit_out})" if hit else \
                f"within {threshold:g}{unit_out} of expected"
        else:
            hit = is_anomaly and direction == direction_bad
            reason = "outside its normal range the bad way" if hit else "within its normal range"
        if hit:
            status = "alert"
        elif is_anomaly and favourable:
            status, reason = "good", "outside its normal range the good way"
        else:
            status = "ok"
    return Judgement(
        status=status, shape=shape, basis=basis, observed=observed, expected=expected, low=low, high=high, z=z,
        direction=direction, is_anomaly=is_anomaly, worse_by=worse_by, unit=unit_out, delta_pct=delta_pct,
        reason=reason, history_points=len(history), observed_on=last_day,
    )

"""V1 target evaluation: one metric, one day, against a floor / target / ceiling. Pure: no I/O, no metric names.

A ratio is judged on pooled parts (sum of numerator / sum of denominator over the window), never on an average of
daily ratios. Metric ids, parts and thresholds are data (monitor.metrics, monitor.target_versions, config/monitoring.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Literal

import yaml

from seleric_swarm.paths import repo_root

Series = dict[str, dict[str, float]]  # metric id -> ISO day -> value
Direction = Literal["down", "up", "both", "none"]
Status = Literal["ok", "below_floor", "below_target", "above_target", "above_ceiling", "untrusted", "no_data"]
Severity = Literal["critical", "warning", "info", "none"]


@dataclass(frozen=True)
class MetricDef:
    metric_id: str
    direction_bad: Direction
    eval_window: Literal["day", "trailing_7d_pooled"]
    kind: Literal["catalogue", "ratio"] = "catalogue"
    numerator_id: str | None = None
    denominator_id: str | None = None
    weight_id: str | None = None
    settles_after_days: int = 0

    def __post_init__(self) -> None:
        if self.kind == "ratio" and not (self.numerator_id and self.denominator_id):
            raise ValueError(f"{self.metric_id}: a ratio metric needs numerator_id and denominator_id")
        if self.eval_window == "trailing_7d_pooled" and self.kind == "catalogue" and not self.weight_id:
            raise ValueError(f"{self.metric_id}: a pooled catalogue metric needs weight_id (or be a ratio of parts)")

    @property
    def part_ids(self) -> list[str]:
        if self.kind == "ratio":
            return [i for i in (self.numerator_id, self.denominator_id) if i]
        return [i for i in (self.metric_id, self.weight_id) if i]


@dataclass(frozen=True)
class Band:
    floor: float | None
    target: float | None
    ceiling: float | None


@dataclass(frozen=True)
class GateConfig:
    return_numerator: str
    return_denominator: str
    return_max_share: float
    spend_metric: str
    spend_lookback_days: int
    spend_min_history_days: int
    spend_min_share: float
    window_days: int
    min_days: int

    @classmethod
    def load(cls, path: str | Path = "config/monitoring.yaml") -> GateConfig:
        p = Path(path)
        data = yaml.safe_load((p if p.is_absolute() else repo_root() / p).read_text(encoding="utf-8")) or {}
        gate, pool = data["trust_gate"], data["pooling"]
        return cls(
            return_numerator=gate["bulk_return"]["numerator_metric"],
            return_denominator=gate["bulk_return"]["denominator_metric"],
            return_max_share=float(gate["bulk_return"]["max_share"]),
            spend_metric=gate["spend_collapse"]["metric"],
            spend_lookback_days=int(gate["spend_collapse"]["lookback_days"]),
            spend_min_history_days=int(gate["spend_collapse"]["min_history_days"]),
            spend_min_share=float(gate["spend_collapse"]["min_share_of_median"]),
            window_days=int(pool["window_days"]),
            min_days=int(pool["min_days"]),
        )


@dataclass(frozen=True)
class DataEvent:
    day: str
    kind: Literal["bulk_return", "spend_collapse"]
    detail: str


@dataclass(frozen=True)
class Evaluation:
    metric_id: str
    day: str
    observed: float | None
    status: Status
    severity: Severity
    days_used: int
    data_status: Literal["fresh", "provisional"]
    reason: str = ""
    explained_by: str = ""  # a registered known period (e.g. ads paused on purpose): shown, not alerted


def _days_back(day: str, n: int) -> list[str]:
    end = date.fromisoformat(day)
    return [(end - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def data_events(series: Series, gate: GateConfig) -> list[DataEvent]:
    """Days that the trust gate sets aside or reports on their own. Missing parts mean no event, never a guess."""
    events: list[DataEvent] = []
    returns, sales = series.get(gate.return_numerator, {}), series.get(gate.return_denominator, {})
    for day in sorted(returns):
        gross = sales.get(day)
        if gross and gross > 0 and returns[day] / gross > gate.return_max_share:
            events.append(DataEvent(day, "bulk_return", f"closed returns {returns[day]:,.0f} on gross sales {gross:,.0f}"))
    spend = series.get(gate.spend_metric, {})
    collapsed: set[str] = set()
    last_base: float | None = None
    for day in sorted(spend):
        # Days already flagged are left out of the baseline, or a long outage would become its own normal.
        prior = [spend[d] for d in _days_back(day, gate.spend_lookback_days + 1)[:-1] if d in spend and d not in collapsed]
        if len(prior) >= gate.spend_min_history_days:
            base = last_base = median(prior)
        elif last_base is not None and _days_back(day, 2)[0] in collapsed:
            base = last_base  # inside an outage that has outlasted the lookback: keep judging against the last good level
        else:
            continue
        if base > 0 and spend[day] < gate.spend_min_share * base:
            collapsed.add(day)
            events.append(DataEvent(day, "spend_collapse", f"spend {spend[day]:,.0f} vs {base:,.0f} median of the prior days"))
    return events


def untrusted_days(events: list[DataEvent]) -> set[str]:
    """Days whose settled-money metrics cannot be judged. A spend collapse is real, so it does not set a day aside."""
    return {e.day for e in events if e.kind == "bulk_return"}


def classify(value: float, band: Band, direction: Direction) -> Status:
    """Position against the band. The good side of a one-way metric is reported (as info), never as a breach."""
    floor, target, ceiling = band.floor, band.target, band.ceiling
    if direction == "none":
        return "ok"
    if direction == "both":
        if floor is not None and value < floor:
            return "below_floor"
        return "above_ceiling" if ceiling is not None and value > ceiling else "ok"
    if direction == "down":
        if floor is not None and value < floor:
            return "below_floor"
        if target is not None and value < target:
            return "below_target"
        return "above_ceiling" if ceiling is not None and value > ceiling else "ok"
    if ceiling is not None and value > ceiling:
        return "above_ceiling"
    if target is not None and value > target:
        return "above_target"
    return "below_floor" if floor is not None and value < floor else "ok"


def severity_of(status: Status, direction: Direction) -> Severity:
    if status in ("ok", "untrusted", "no_data") or direction == "none":
        return "none"
    if direction == "both":
        return "warning"  # no single bad direction (e.g. spend): a breach is information for a human, not an emergency
    bad_hard, bad_soft = ("below_floor", "below_target") if direction == "down" else ("above_ceiling", "above_target")
    if status == bad_hard:
        return "critical"
    return "warning" if status == bad_soft else "info"  # the good side: unusually high / low, check the data


def observed_value(metric: MetricDef, series: Series, day: str, skip: set[str], gate: GateConfig) -> tuple[float | None, int]:
    """The metric's value for ``day`` and how many days it rests on. Days in ``skip`` are left out of pooled sums."""
    if metric.eval_window == "day":
        value = series.get(metric.metric_id, {}).get(day)
        return value, (0 if value is None else 1)
    days = [d for d in _days_back(day, gate.window_days) if d not in skip]
    if metric.kind == "ratio":
        num, den = series.get(metric.numerator_id or "", {}), series.get(metric.denominator_id or "", {})
        used = [d for d in days if d in num and d in den]
        total = sum(den[d] for d in used)
        if len(used) < gate.min_days or total == 0:
            return None, len(used)
        return sum(num[d] for d in used) / total, len(used)
    value_s, weight_s = series.get(metric.metric_id, {}), series.get(metric.weight_id or "", {})
    used = [d for d in days if d in value_s and d in weight_s]
    total = sum(weight_s[d] for d in used)
    if len(used) < gate.min_days or total == 0:
        return None, len(used)
    return sum(value_s[d] * weight_s[d] for d in used) / total, len(used)


def evaluate(
    metric: MetricDef,
    band: Band | None,
    series: Series,
    day: str,
    *,
    untrusted: set[str],
    gate: GateConfig,
    as_of: str,
    explained: dict[str, str] | None = None,
) -> Evaluation:
    """One metric on one day. ``as_of`` is the latest day in the data; younger days of a late-settling metric are provisional."""
    settles = metric.settles_after_days > 0
    data_status = "provisional" if settles and (date.fromisoformat(as_of) - date.fromisoformat(day)).days < metric.settles_after_days else "fresh"
    if settles and metric.eval_window == "day" and day in untrusted:
        return Evaluation(metric.metric_id, day, None, "untrusted", "none", 0, data_status, "bulk return closure on this day")
    skip = untrusted if settles else set()
    value, used = observed_value(metric, series, day, skip, gate)
    if value is None:
        return Evaluation(metric.metric_id, day, None, "no_data", "none", used, data_status, "not enough trusted days")
    if band is None:
        return Evaluation(metric.metric_id, day, value, "ok", "none", used, data_status, "no target set")
    status = classify(value, band, metric.direction_bad)
    severity = severity_of(status, metric.direction_bad)
    reason = (explained or {}).get(day, "")
    if reason and severity in ("critical", "warning"):
        severity = "info"
    return Evaluation(metric.metric_id, day, value, status, severity, used, data_status, explained_by=reason if severity == "info" else "")


def evaluation_details(e: Evaluation) -> dict[str, Any]:
    return {"days_used": e.days_used, "reason": e.reason}

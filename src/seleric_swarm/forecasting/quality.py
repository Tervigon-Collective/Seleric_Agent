"""Data-quality gates per series — verdicts: block, mask, drop, warn."""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.forecasting.types import QualityVerdict
from seleric_swarm.paths import repo_root

SeriesRole = Literal["target", "feature"]


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    start: str
    end: str
    metrics: list[str] = Field(default_factory=list)
    reason: str = ""


class IncidentsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    incidents: list[Incident] = Field(default_factory=list)


@lru_cache(maxsize=1)
def load_incidents(path: str | None = None) -> IncidentsFile:
    p = Path(path) if path else repo_root() / "config" / "data_incidents.yaml"
    if not p.exists():
        return IncidentsFile()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return IncidentsFile.model_validate(data)


def clear_incidents_cache() -> None:
    load_incidents.cache_clear()


def _observed(values: list[float | None]) -> list[float]:
    return [float(v) for v in values if v is not None and math.isfinite(float(v))]


def assess_series(
    series: str,
    values: list[float | None],
    index: list[date],
    *,
    role: SeriesRole = "target",
    min_history_days: int = 120,
    expected_last_day: date | None = None,
    nonnegative: bool = False,
    min_volume_share: float | None = None,
    account_total: float | None = None,
    incidents: IncidentsFile | None = None,
) -> tuple[list[float | None], list[QualityVerdict]]:
    """Run quality gates; return (possibly masked values, verdicts)."""
    out = list(values)
    verdicts: list[QualityVerdict] = []
    observed = _observed(out)
    n_present = sum(1 for v in out if v is not None)

    # Q_STALE
    if expected_last_day is not None and index:
        last_present = None
        for d, v in zip(reversed(index), reversed(out), strict=True):
            if v is not None:
                last_present = d
                break
        if last_present is None or last_present < expected_last_day:
            action = "block" if role == "target" else "drop"
            verdicts.append(
                QualityVerdict(
                    code="Q_STALE",
                    series=series,
                    action=action,
                    detail=f"last data day {last_present} earlier than expected {expected_last_day}",
                )
            )
            if action == "block":
                return out, verdicts

    # Q_SHORT_HISTORY
    if len(observed) < min_history_days:
        action = "block" if role == "target" else "drop"
        verdicts.append(
            QualityVerdict(
                code="Q_SHORT_HISTORY",
                series=series,
                action=action,
                detail=f"{len(observed)} observed days < min_history_days={min_history_days}",
            )
        )
        if action == "block":
            return out, verdicts

    # Q_COVERAGE — density from the *first observed day*.
    # Block on the *recent* window (last 90 observed-span days) when under 90%:
    # that is what the forecast actually leans on. The full span only blocks
    # below 80% (live net_sales was 624/695 ≈ 89.8% over two years — a few
    # historical gaps — and wrongly refused under a flat 90% rule).
    first_i = next((i for i, v in enumerate(out) if v is not None), None)
    if first_i is not None:
        span = out[first_i:]
        span_present = sum(1 for v in span if v is not None)
        recent_n = min(90, len(span))
        recent = span[-recent_n:]
        recent_present = sum(1 for v in recent if v is not None)
        recent_ratio = recent_present / recent_n if recent_n else 1.0
        full_ratio = span_present / len(span) if span else 1.0
        if recent_n and recent_ratio < 0.90:
            action = "block" if role == "target" else "drop"
            verdicts.append(
                QualityVerdict(
                    code="Q_COVERAGE",
                    series=series,
                    action=action,
                    detail=(
                        f"recent coverage {recent_present}/{recent_n} "
                        f"({recent_ratio:.1%}) under 90% "
                        f"(full span {span_present}/{len(span)} from "
                        f"{index[first_i].isoformat()})"
                    ),
                )
            )
            if action in {"block", "drop"}:
                return out, verdicts
        if span and full_ratio < 0.80:
            action = "block" if role == "target" else "drop"
            verdicts.append(
                QualityVerdict(
                    code="Q_COVERAGE",
                    series=series,
                    action=action,
                    detail=(
                        f"full-span coverage {span_present}/{len(span)} "
                        f"({full_ratio:.1%}) under 80% from "
                        f"{index[first_i].isoformat()}"
                    ),
                )
            )
            if action in {"block", "drop"}:
                return out, verdicts
        if span and full_ratio < 0.90:
            verdicts.append(
                QualityVerdict(
                    code="Q_COVERAGE",
                    series=series,
                    action="warn",
                    detail=(
                        f"full-span coverage {span_present}/{len(span)} "
                        f"({full_ratio:.1%}) under 90% (recent ok at "
                        f"{recent_ratio:.1%}); proceeding"
                    ),
                )
            )
    elif index:
        action = "block" if role == "target" else "drop"
        verdicts.append(
            QualityVerdict(
                code="Q_COVERAGE",
                series=series,
                action=action,
                detail="no observed days in context window",
            )
        )
        if action in {"block", "drop"}:
            return out, verdicts

    # Q_NEGATIVE
    if nonnegative:
        for i, v in enumerate(out):
            if v is not None and v < 0:
                verdicts.append(
                    QualityVerdict(
                        code="Q_NEGATIVE",
                        series=series,
                        action="block",
                        detail=f"negative value {v} on {index[i].isoformat()}",
                        dates=[index[i].isoformat()],
                    )
                )
                return out, verdicts

    # Q_OUTAGE_ZERO_RUN — mask consecutive zeros where the typical positive
    # level (p05 of strictly-positive observations) is above 0.
    positive = [v for v in observed if v > 0]
    if len(positive) >= 20:
        try:
            p05 = statistics.quantiles(positive, n=20)[0]  # ~5th pct
        except statistics.StatisticsError:
            p05 = 0.0
        if p05 > 0:
            run_start = None
            for i, v in enumerate(out):
                if v == 0:
                    if run_start is None:
                        run_start = i
                else:
                    if run_start is not None and i - run_start >= 2:
                        dates = [index[j].isoformat() for j in range(run_start, i)]
                        for j in range(run_start, i):
                            out[j] = None
                        verdicts.append(
                            QualityVerdict(
                                code="Q_OUTAGE_ZERO_RUN",
                                series=series,
                                action="mask",
                                detail=f"masked {i - run_start} consecutive zeros (p05={p05:.4g})",
                                dates=dates,
                            )
                        )
                    run_start = None
            if run_start is not None and len(out) - run_start >= 2:
                dates = [index[j].isoformat() for j in range(run_start, len(out))]
                for j in range(run_start, len(out)):
                    out[j] = None
                verdicts.append(
                    QualityVerdict(
                        code="Q_OUTAGE_ZERO_RUN",
                        series=series,
                        action="mask",
                        detail=f"masked {len(out) - run_start} trailing zeros",
                        dates=dates,
                    )
                )

    # Q_INCIDENT
    incidents = incidents or load_incidents()
    for inc in incidents.incidents:
        if inc.metrics and series not in inc.metrics:
            continue
        try:
            start = date.fromisoformat(inc.start[:10])
            end = date.fromisoformat(inc.end[:10])
        except ValueError:
            continue
        dates: list[str] = []
        for i, d in enumerate(index):
            if start <= d <= end and out[i] is not None:
                out[i] = None
                dates.append(d.isoformat())
        if dates:
            verdicts.append(
                QualityVerdict(
                    code="Q_INCIDENT",
                    series=series,
                    action="mask",
                    detail=f"{inc.id}: {inc.reason or 'listed incident'}",
                    dates=dates,
                )
            )

    # Q_LEVEL_DIP — a multi-day collapse well below the series' own recent level
    # that then recovers is an outage, not demand: masking it stops the model from
    # learning "the new normal is the trough". An unrecovered drop (still in
    # progress at the end of the series) is left alone: it may be a real shift.
    dip_dates: list[str] = []
    i = 0
    while i < len(out):
        prior = [v for v in out[max(0, i - 60) : i] if v is not None][-28:]
        v = out[i]
        if v is None or len(prior) < 14:
            i += 1
            continue
        base = statistics.median(prior)
        if base > 0 and v < 0.5 * base:
            j = i
            while j < len(out) and (out[j] is None or out[j] < 0.5 * base):
                j += 1
            after = [x for x in out[j : j + 14] if x is not None][:7]
            run_obs = sum(1 for x in out[i:j] if x is not None)
            if 3 <= run_obs <= 28 and len(after) >= 3 and statistics.median(after) >= 0.6 * base:
                for k in range(i, j):
                    if out[k] is not None:
                        dip_dates.append(index[k].isoformat())
                        out[k] = None
            i = j
        else:
            i += 1
    if dip_dates:
        verdicts.append(
            QualityVerdict(
                code="Q_LEVEL_DIP",
                series=series,
                action="mask",
                detail=(
                    f"{len(dip_dates)} day(s) fell below half of the recent level and then "
                    "recovered; treated as an outage and excluded from training"
                ),
                dates=dip_dates,
            )
        )

    # Q_OUTLIER — warn only
    obs_now = _observed(out)
    if len(obs_now) >= 30:
        med = statistics.median(obs_now)
        mad = statistics.median([abs(x - med) for x in obs_now]) or 1.0
        outlier_dates: list[str] = []
        for i, v in enumerate(out):
            if v is None:
                continue
            z = 0.6745 * (v - med) / mad
            if abs(z) > 6:
                outlier_dates.append(index[i].isoformat())
        if outlier_dates:
            verdicts.append(
                QualityVerdict(
                    code="Q_OUTLIER",
                    series=series,
                    action="warn",
                    detail=f"{len(outlier_dates)} point(s) with robust z > 6",
                    dates=outlier_dates,
                )
            )

    # Q_LEVEL_SHIFT — warn (simple mean split last 90 vs prior)
    if len(index) >= 120 and len(obs_now) >= 60:
        recent_cut = index[-1] - timedelta(days=90)
        recent = [v for d, v in zip(index, out, strict=True) if d > recent_cut and v is not None]
        prior = [v for d, v in zip(index, out, strict=True) if d <= recent_cut and v is not None]
        if len(recent) >= 14 and len(prior) >= 30:
            m_r, m_p = statistics.mean(recent), statistics.mean(prior)
            if m_p != 0 and abs(m_r - m_p) / abs(m_p) > 0.5:
                verdicts.append(
                    QualityVerdict(
                        code="Q_LEVEL_SHIFT",
                        series=series,
                        action="warn",
                        detail=f"mean shift {(m_r - m_p) / m_p:.0%} in last 90 days",
                    )
                )

    # Q_CONSTANT
    if role == "feature" and len(obs_now) >= 2 and len(set(obs_now)) == 1:
        verdicts.append(
            QualityVerdict(
                code="Q_CONSTANT",
                series=series,
                action="drop",
                detail="zero variance",
            )
        )
        return out, verdicts

    # Q_SPARSE_ENTITY
    if min_volume_share is not None and account_total is not None and account_total > 0:
        share = sum(obs_now) / account_total if obs_now else 0.0
        zero_share = sum(1 for v in out if v == 0) / len(out) if out else 1.0
        if zero_share > 0.5 or share < min_volume_share:
            verdicts.append(
                QualityVerdict(
                    code="Q_SPARSE_ENTITY",
                    series=series,
                    action="refuse",
                    detail=f"zero_share={zero_share:.0%} volume_share={share:.0%}",
                )
            )

    if not verdicts:
        verdicts.append(
            QualityVerdict(code="Q_OK", series=series, action="pass", detail="ok")
        )
    return out, verdicts


def is_blocking(verdicts: Iterable[QualityVerdict]) -> bool:
    return any(v.action in {"block", "refuse"} for v in verdicts)


def dropped_features(verdicts: Iterable[QualityVerdict]) -> set[str]:
    return {v.series for v in verdicts if v.action == "drop"}

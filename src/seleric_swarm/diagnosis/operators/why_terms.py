"""The rule-based classification of a driver estimate (supported_cause / likely_contributor / correlation /
insufficient_evidence), the calendar-adjusted correlation that groups inseparable drivers, and the queued driver
record (moved from ``causal/diagnosis.py``, plan P2.2; D-11)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from seleric_swarm.diagnosis.detect.calendar import fit_calendar
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.estimates import TimeSeriesEffect
from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _mean
from seleric_swarm.diagnosis.domain.report import Classification


def _classify_driver(
    est: TimeSeriesEffect, moved: bool, aligned: bool, *, cfg: DiagnosisConfig
) -> tuple[Classification, str]:
    lo, hi = (est.confidence_interval + [math.nan, math.nan])[:2]
    significant = _finite(lo) and _finite(hi) and (lo > 0 or hi < 0)
    refs = {r["name"]: bool(r.get("passed")) for r in est.refutations}
    placebo_ok = refs.get("time_shift_placebo", False) and refs.get("future_treatment_placebo", False)
    all_ok = bool(refs) and all(refs.values())
    direction = est.temporal.get("direction")
    naive_p = est.naive_association.get("p_value")
    if not significant:
        if naive_p is not None and naive_p < cfg.alpha:
            return "correlation", "raw association disappears once calendar, lags and competing drivers are adjusted for"
        return "correlation", "no adjusted effect distinguishable from zero"
    if direction == "feedback":
        return "correlation", "past outcome predicts this driver (feedback / reverse causality), so direction is not identified"
    if not placebo_ok:
        failed = [n for n in ("time_shift_placebo", "future_treatment_placebo") if not refs.get(n, False)]
        return "correlation", f"placebo refuter failed: {', '.join(failed)}"
    strong_direction = direction in ("temporal", "intervention")
    if not strong_direction:
        return "correlation", (
            "same-day association only: the data cannot tell whether this moved the outcome or the outcome "
            "(or a shared cause) moved it"
        )
    if all_ok and moved and aligned:
        basis = (
            "past values of the driver predict the outcome, not the reverse"
            if direction == "temporal"
            else "repeated abrupt shifts in the driver were followed by the outcome, which had not moved first"
        )
        return "supported_cause", f"identified effect, all refuters pass, driver moved at the event; {basis}"
    reasons = []
    if not all_ok:
        reasons.append("robustness check failed: " + ", ".join(n for n, ok in refs.items() if not ok))
    return "likely_contributor", "; ".join(reasons) or "effect identified"


def _residual_correlation(a: Series, b: Series, days: list[date], *, cfg: DiagnosisConfig) -> float | None:
    fa, fb = fit_calendar(a, days, cfg=cfg), fit_calendar(b, days, cfg=cfg)
    if fa is None or fb is None:
        return None
    common = [d for d in days if d in fa.residual_z and d in fb.residual_z]
    if len(common) < cfg.min_observation_rows:
        return None
    xa = [fa.residual_z[d] for d in common]
    xb = [fb.residual_z[d] for d in common]
    ma, mb = _mean(xa), _mean(xb)
    sa = math.sqrt(sum((x - ma) ** 2 for x in xa))
    sb = math.sqrt(sum((x - mb) ** 2 for x in xb))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(xa, xb, strict=True)) / (sa * sb)


@dataclass
class _PendingDriver:
    """A driver that moved at the event: its estimate is queued and read once all of them are in."""

    driver: str
    kwargs: dict[str, Any]
    moved: bool
    x_z: float | None
    x_ev: float | None
    x_rf: float | None
    dx: float

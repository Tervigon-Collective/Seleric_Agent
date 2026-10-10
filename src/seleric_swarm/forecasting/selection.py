"""Data-driven choice of the driver that conditions a forecast's level.

Candidates come from the policy/priors pool (never from metric-name rules). Each
candidate is scored by a rolling-origin backtest on the target's own history:
at past origins, predict the next-horizon total from the candidate's recent
run-rate times the target's trailing efficiency, using only data that was
available at that origin, and compare with the statistical baseline (recent
target level). A candidate is chosen only if it beats the baseline.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

HORIZON = 14
ORIGIN_STEP = 7
LOOKBACK_DAYS = 270
EFF_DAYS = 42
RUN_DAYS = 14
BASE_DAYS = 28
MIN_ORIGINS = 6
MIN_GAIN = 0.05  # must cut median error by at least 5% relative to baseline


@dataclass
class CandidateScore:
    metric_id: str
    median_error: float
    baseline_error: float
    origins: int

    @property
    def gain(self) -> float:
        return 1.0 - self.median_error / self.baseline_error if self.baseline_error > 0 else 0.0


@dataclass
class Selection:
    chosen: str | None = None
    scores: list[CandidateScore] = field(default_factory=list)
    baseline_error: float | None = None
    note: str = ""


def _score(
    target: dict[date, float], cand: dict[date, float], *, last_origin: date, lag: int, horizon: int = HORIZON
) -> tuple[list[float], list[float]]:
    errs: list[float] = []
    base_errs: list[float] = []
    o = last_origin
    first = last_origin - timedelta(days=LOOKBACK_DAYS)
    while o > first:
        days = [o + timedelta(i) for i in range(horizon)]
        got = [target.get(d) for d in days]
        if sum(v is not None for v in got) >= horizon - 2:
            actual = sum(v for v in got if v is not None) * horizon / sum(v is not None for v in got)
            cut = o - timedelta(days=lag)
            eff_days = [cut - timedelta(i) for i in range(EFF_DAYS)]
            pairs = [(target[d], cand[d]) for d in eff_days if d in target and d in cand]
            run = [cand[o - timedelta(i + 1)] for i in range(RUN_DAYS) if (o - timedelta(i + 1)) in cand]
            base_vals = [target[cut - timedelta(i)] for i in range(BASE_DAYS) if (cut - timedelta(i)) in target]
            if len(pairs) >= 14 and len(run) >= 7 and len(base_vals) >= 14 and actual > 0:
                ds = sum(c for _, c in pairs)
                if ds > 0:
                    eff = sum(t for t, _ in pairs) / ds
                    pred = statistics.mean(run) * eff * horizon
                    errs.append(abs(pred - actual) / actual)
                    base_errs.append(abs(statistics.mean(base_vals) * horizon - actual) / actual)
        o -= timedelta(days=ORIGIN_STEP)
    return errs, base_errs


def select_driver(
    target_values: list[float | None],
    target_dates: list[date],
    candidates: dict[str, dict[str, float]],
    *,
    as_of: date,
    lag: int,
) -> Selection:
    """Pick the candidate whose level projection backtests best; None if none beats baseline."""
    target = {d: v for d, v in zip(target_dates, target_values, strict=False) if v is not None}
    sel = Selection()
    if len(target) < 120:
        sel.note = "history too short to compare candidate features"
        return sel
    last_origin = max(target) - timedelta(days=HORIZON)
    for mid, series in candidates.items():
        cand = {date.fromisoformat(k[:10]): v for k, v in series.items()}
        if len(cand) < 90 or len(set(round(v, 6) for v in cand.values())) < 5:
            continue
        errs, base = _score(target, cand, last_origin=last_origin, lag=lag)
        if len(errs) < MIN_ORIGINS:
            continue
        sel.scores.append(
            CandidateScore(mid, statistics.median(errs), statistics.median(base), len(errs))
        )
    sel.scores.sort(key=lambda s: s.median_error)
    if sel.scores:
        sel.baseline_error = sel.scores[0].baseline_error
        best = sel.scores[0]
        if best.gain >= MIN_GAIN:
            sel.chosen = best.metric_id
        else:
            sel.note = "no candidate feature beat the recent-level baseline in backtest"
    else:
        sel.note = "no candidate feature had enough overlapping history"
    return sel


def selection_facts(sel: Selection, label_of: Any) -> str:
    if not sel.scores:
        return f"- feature selection: {sel.note}"
    top = ", ".join(
        f"{label_of(s.metric_id)} ({s.median_error:.0%} typical 14-day error)" for s in sel.scores[:4]
    )
    if sel.chosen:
        best = sel.scores[0]
        return (
            f"- feature selection (rolling backtest over ~{best.origins} past origins): considered {top}; "
            f"chose {label_of(sel.chosen)} because it cut the typical 14-day total error from "
            f"{best.baseline_error:.0%} (recent-level baseline) to {best.median_error:.0%}"
        )
    return f"- feature selection: considered {top}; none beat the recent-level baseline ({sel.baseline_error:.0%}), so none was used"

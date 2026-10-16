"""Does the brand's own history confirm a reviewed mechanism edge? (plan D-56)

An edge ``source -> target`` of the reviewed priors is a business claim: a move in the source moves the target, in a stated
direction, within a stated lag. The engine uses it for a brand only if the pre-event history agrees: the target regressed on
the source (lagged by each lag of the edge's window), weekday, a trend and the target's own previous day, with
autocorrelation-robust errors; the best lag counts, its p-value (one-sided when the prior states a direction) is multiplied by the number of
lags tried, the stated sign must hold, and the false-discovery rate is controlled across the edges tested for the question (Benjamini-Hochberg).

What "verified" means, and what it does not: at lag 0 the test shows the sign and a conditional association, not which way
the arrow runs (that stays with the temporal-precedence test the estimator applies); a source that takes fewer than
``edge_min_distinct`` values has no variation to learn from, which is "unverifiable", not "failed"; an edge the reviewers call
structural is a definition of the business (a unit shipped costs shipping), not a claim, and is not tested."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.errors import DiagnosisError
from seleric_swarm.diagnosis.domain.numeric import Series, _finite
from seleric_swarm.diagnosis.domain.subgraph import EdgeSpec
from seleric_swarm.diagnosis.libs.hac import ols_hac

Status = Literal["verified", "definitional", "not_confirmed", "wrong_sign", "no_variation", "too_little_data", "no_series"]
VERIFIED = frozenset({"verified", "definitional"})
_WEEKDAYS = range(1, 7)


@dataclass(frozen=True)
class EdgeVerdict:
    source: str
    target: str
    status: Status
    lag: int | None = None
    beta: float | None = None
    p_raw: float | None = None  # best lag, multiplied by the number of lags tried
    p_adj: float | None = None  # after the false-discovery correction across the edges tested
    n_rows: int = 0

    @property
    def usable(self) -> bool:
        return self.status in VERIFIED

    def describe(self) -> str:
        why = {
            "not_confirmed": "no effect found in the history", "wrong_sign": "the history shows the opposite direction",
            "no_variation": "the source barely varies, so there is nothing to learn from",
            "too_little_data": "too little shared history", "no_series": "no data for one side",
        }.get(self.status, self.status)
        return f"{self.source} -> {self.target}: {why}"


def _best_lag(spec: EdgeSpec, source: Series, target: Series, days: Sequence[date], cfg: DiagnosisConfig) -> tuple[Status, dict[str, float]]:
    best: dict[str, float] | None = None
    tried = spec.lag_days[1] - spec.lag_days[0] + 1
    for lag in range(spec.lag_days[0], spec.lag_days[1] + 1):
        rows = [
            d for d in days
            if _finite(target.get(d)) and _finite(target.get(d - timedelta(days=1))) and _finite(source.get(d - timedelta(days=lag)))
        ]
        if len(rows) < cfg.edge_min_rows:
            continue
        x = [float(source[d - timedelta(days=lag)]) for d in rows]
        if len(set(x)) < cfg.edge_min_distinct:
            return "no_variation", {}
        columns = {"x": x, "own_lag": [float(target[d - timedelta(days=1)]) for d in rows], "trend": [float((d - rows[0]).days) for d in rows]}
        columns.update({f"weekday_{k}": [1.0 if d.weekday() == k else 0.0 for d in rows] for k in _WEEKDAYS})
        try:
            fit = ols_hac([float(target[d]) for d in rows], columns)
        except DiagnosisError:
            continue
        beta, p_two = fit.params["x"], fit.pvalues["x"]
        # The prior states a direction, so the claim tested is one-sided: half the two-sided p when the history agrees on the
        # sign, the rest when it does not. An edge with no stated direction (+/-) keeps the two-sided p.
        agrees = spec.sign == "+/-" or (beta > 0) == (spec.sign == "+")
        p_claim = p_two if spec.sign == "+/-" else (p_two / 2 if agrees else 1 - p_two / 2)
        found = {"lag": float(lag), "beta": beta, "p": p_claim, "p_two": p_two, "n": float(fit.n_rows)}
        if best is None or found["p"] < best["p"]:
            best = found
    if best is None:
        return "too_little_data", {}
    best["p"] = min(1.0, best["p"] * tried)
    return "not_confirmed", best


def _benjamini_hochberg(p_values: Mapping[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    order = sorted(p_values, key=lambda k: p_values[k])
    n = len(order)
    adjusted: dict[tuple[str, str], float] = {}
    running = 1.0
    for rank in range(n, 0, -1):
        key = order[rank - 1]
        running = min(running, p_values[key] * n / rank)
        adjusted[key] = running
    return adjusted


def verify_edges(
    specs: Sequence[EdgeSpec], series: Mapping[str, Series], days: Sequence[date], cfg: DiagnosisConfig
) -> dict[tuple[str, str], EdgeVerdict]:
    """A verdict for every spec (one per source/target pair)."""
    verdicts: dict[tuple[str, str], EdgeVerdict] = {}
    raw: dict[tuple[str, str], tuple[Status, dict[str, float], EdgeSpec]] = {}
    for spec in specs:
        key = (spec.source, spec.target)
        if key in verdicts or key in raw:
            continue
        if spec.structural:
            verdicts[key] = EdgeVerdict(spec.source, spec.target, "definitional")
        elif spec.source not in series or spec.target not in series:
            verdicts[key] = EdgeVerdict(spec.source, spec.target, "no_series")
        else:
            tested, found = _best_lag(spec, series[spec.source], series[spec.target], days, cfg)
            if found:
                raw[key] = (tested, found, spec)
            else:
                verdicts[key] = EdgeVerdict(spec.source, spec.target, tested)
    adjusted = _benjamini_hochberg({k: v[1]["p"] for k, v in raw.items()})
    for key, (_, found, spec) in raw.items():
        sign_ok = spec.sign == "+/-" or (found["beta"] > 0) == (spec.sign == "+")
        status: Status = (
            "verified" if sign_ok and adjusted[key] < cfg.edge_alpha
            else ("wrong_sign" if not sign_ok and found["p_two"] < cfg.edge_alpha else "not_confirmed")
        )
        verdicts[key] = EdgeVerdict(
            spec.source, spec.target, status, int(found["lag"]), found["beta"], found["p"], adjusted[key], int(found["n"]),
        )
    return verdicts

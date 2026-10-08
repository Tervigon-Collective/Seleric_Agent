"""Statistical primitives for insight extraction — pure functions, no I/O.

Each primitive answers one "is this pattern unusual?" question and returns a
p-value under a stated null hypothesis, so insights of different kinds can be
ranked on one scale and corrected together for multiple testing. Sources:

- **Outstanding segment** — Tang et al., *Extracting Top-K Insights from
  Multi-dimensional Data* (SIGMOD 2017), §3.2: H0 "the values follow a power law
  with Gaussian noise"; fit the power law to every value but the largest and
  ask how surprising the largest one's residual is. Fitted here in log space
  (``log x = a + b log rank``), which keeps the residuals on one scale whatever
  the magnitude.
- **Trend** — Tang §3.2 shape insight: H0 "slope ≈ 0", weighted by goodness of
  fit. The slope's p-value comes from OLS with day-of-week controls and HAC
  (Newey-West) errors instead of Tang's corpus-fitted logistic, because daily
  business series are seasonal and autocorrelated.
- **Distribution shift** — Bhagwan et al., *Adtributor* (NSDI 2014), §3:
  explanatory power ``EP_j = (A_j - F_j) / (A - F)`` and Jensen-Shannon surprise
  ``S_j = 0.5 (p log 2p/(p+q) + q log 2q/(p+q))``, greedy succinct set per
  dimension (``T_EEP`` per element, ``T_EP`` cumulative). The dimension's
  significance is judged against the JS divergence between consecutive windows
  in its own history, so a naturally volatile mix does not look like news.
- **Window change** — a normal prediction-interval t-test of this window's
  change against the changes between earlier windows (small samples).
- **Change point** — a single mean-shift scan over day-of-week-adjusted
  values, Bonferroni-corrected over the candidate split points.
- **Multiple testing** — Benjamini-Hochberg over every test a run performs
  (Zhao et al., *Controlling False Discoveries During Interactive Data
  Exploration*, SIGMOD 2017, motivates why exploration needs this at all).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

Series = dict[date, float]

_EPS = 1e-12


def normal_sf(z: float) -> float:
    """P(Z > z) for a standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def two_sided_p(z: float) -> float:
    return min(1.0, 2.0 * normal_sf(abs(z)))


def prediction_t(observed: float, history: list[float]) -> tuple[float, int] | None:
    """(t, df) of a new observation against a small sample of earlier ones.

    The exact normal prediction interval: ``t = (x - mean) / (s * sqrt(1 + 1/n))``
    with ``n - 1`` degrees of freedom. With the five or six windows an
    exploration has, a median/MAD scale is too unstable (it ran anti-conservative
    under pure noise); an outlier in the history only widens ``s``, which errs
    toward reporting less."""
    xs = [x for x in history if math.isfinite(x)]
    if len(xs) < 3 or not math.isfinite(observed):
        return None
    n = len(xs)
    mean = float(np.mean(xs))
    sd = float(np.std(xs, ddof=1))
    if sd <= _EPS:
        sd = max(abs(mean) * 0.01, _EPS)
    return (observed - mean) / (sd * math.sqrt(1 + 1 / n)), n - 1


def t_tail(t: float, df: int, *, two_sided: bool) -> float:
    from scipy import stats

    tail = float(stats.t.sf(abs(t) if two_sided else t, max(1, df)))
    return min(1.0, 2.0 * tail if two_sided else tail)


# --------------------------------------------------------------------------- outstanding
@dataclass(frozen=True)
class Outstanding:
    top: str
    top_value: float
    runner_up: str
    runner_up_value: float
    share: float
    p_value: float


def outstanding_top(values: dict[str, float], min_segments: int = 5) -> Outstanding | None:
    """Is the largest segment remarkably larger than a power law over the rest predicts?"""
    pos = sorted(((k, v) for k, v in values.items() if math.isfinite(v) and v > 0), key=lambda kv: -kv[1])
    if len(pos) < min_segments:
        return None
    total = sum(v for _, v in pos)
    ranks = np.arange(1, len(pos) + 1, dtype=float)
    logx = np.log([v for _, v in pos])
    # Fit log x = a + b log r on ranks 2..n; predict rank 1.
    b, a = np.polyfit(np.log(ranks[1:]), logx[1:], 1)
    resid = logx[1:] - (a + b * np.log(ranks[1:]))
    sigma = float(np.std(resid, ddof=2)) if len(resid) > 2 else 0.0
    sigma = max(sigma, 0.05)  # a perfect fit on few points must not make every top "infinitely" surprising
    r_max = float(logx[0] - a)  # log(rank 1) == 0
    p = normal_sf((r_max - float(np.mean(resid))) / sigma)
    return Outstanding(
        top=pos[0][0], top_value=pos[0][1], runner_up=pos[1][0], runner_up_value=pos[1][1],
        share=pos[0][1] / total if total > 0 else 0.0, p_value=p,
    )


# --------------------------------------------------------------------------- trend
@dataclass(frozen=True)
class Trend:
    slope_per_day: float
    relative_per_week: float  # slope * 7 / mean level
    partial_r2: float
    p_value: float
    n_days: int


def _dow_design(days: list[date]) -> np.ndarray:
    cols = [np.ones(len(days))]
    for k in range(1, 7):
        cols.append(np.array([1.0 if d.weekday() == k else 0.0 for d in days]))
    return np.column_stack(cols)


def trend(series: Series, min_days: int = 21) -> Trend | None:
    """Linear trend in a daily series, controlling for day of week (HAC errors)."""
    days = sorted(d for d, v in series.items() if math.isfinite(v))
    if len(days) < min_days:
        return None
    y = np.array([series[d] for d in days], dtype=float)
    mean = float(np.mean(y))
    if abs(mean) <= _EPS or float(np.std(y)) <= _EPS:
        return None
    t = np.array([(d - days[0]).days for d in days], dtype=float)
    reduced = _dow_design(days)
    full = np.column_stack([reduced, t])
    import statsmodels.api as sm

    try:
        fit = sm.OLS(y, full).fit(cov_type="HAC", cov_kwds={"maxlags": 7})
    except Exception:
        return None
    ssr_full = float(fit.ssr)
    ssr_reduced = float(np.sum((y - reduced @ np.linalg.lstsq(reduced, y, rcond=None)[0]) ** 2))
    partial = max(0.0, (ssr_reduced - ssr_full) / ssr_reduced) if ssr_reduced > _EPS else 0.0
    slope = float(fit.params[-1])
    p = float(fit.pvalues[-1])
    if not math.isfinite(p):
        return None
    return Trend(slope_per_day=slope, relative_per_week=slope * 7 / mean, partial_r2=partial, p_value=p, n_days=len(days))


# --------------------------------------------------------------------------- change point
@dataclass(frozen=True)
class ChangePoint:
    first_day: date  # first day of the new level
    before: float  # mean daily value before (day-of-week adjusted level + overall mean)
    after: float
    relative: float
    p_value: float


def change_point(series: Series, min_segment: int = 7) -> ChangePoint | None:
    """Strongest single mean shift in a daily series after removing day-of-week effects."""
    days = sorted(d for d, v in series.items() if math.isfinite(v))
    n = len(days)
    if n < 2 * min_segment + 1:
        return None
    y = np.array([series[d] for d in days], dtype=float)
    level = float(np.mean(y))
    if abs(level) <= _EPS:
        return None
    dow_med = {k: float(np.median([y[i] for i, d in enumerate(days) if d.weekday() == k] or [level])) for k in range(7)}
    resid = np.array([y[i] - dow_med[d.weekday()] for i, d in enumerate(days)])
    best: tuple[float, int] | None = None
    candidates = range(min_segment, n - min_segment + 1)
    for k in candidates:
        a, b = resid[:k], resid[k:]
        va, vb = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
        se = math.sqrt(va / len(a) + vb / len(b))
        if se <= _EPS:
            continue
        tstat = (float(np.mean(b)) - float(np.mean(a))) / se
        if best is None or abs(tstat) > abs(best[0]):
            best = (tstat, k)
    if best is None:
        return None
    tstat, k = best
    # Bonferroni over every split examined: the max of many t-stats is not one t-stat.
    from scipy import stats

    p = min(1.0, len(candidates) * 2.0 * float(stats.t.sf(abs(tstat), n - 2)))
    before, after = float(np.mean(y[:k])), float(np.mean(y[k:]))
    rel = (after - before) / abs(before) if abs(before) > _EPS else 0.0
    return ChangePoint(first_day=days[k], before=before, after=after, relative=rel, p_value=p)


# --------------------------------------------------------------------------- distribution shift
def js_term(p: float, q: float) -> float:
    """One element's Jensen-Shannon contribution (Adtributor eq. 7); 0 <= S <= 1 with log base 2."""
    out = 0.0
    m = p + q
    if p > 0:
        out += p * math.log2(2 * p / m)
    if q > 0:
        out += q * math.log2(2 * q / m)
    return 0.5 * out


def js_divergence(prior: dict[str, float], current: dict[str, float]) -> float | None:
    """JS divergence between two segment distributions (values are normalised here)."""
    keys = set(prior) | set(current)
    f = sum(max(prior.get(k, 0.0), 0.0) for k in keys)
    a = sum(max(current.get(k, 0.0), 0.0) for k in keys)
    if f <= _EPS or a <= _EPS:
        return None
    return sum(js_term(max(prior.get(k, 0.0), 0.0) / f, max(current.get(k, 0.0), 0.0) / a) for k in keys)


@dataclass(frozen=True)
class SegmentShift:
    segment: str
    previous: float
    current: float
    previous_share: float
    current_share: float
    explanatory_power: float | None  # share of the total change this segment carries
    surprise: float


@dataclass(frozen=True)
class Shift:
    explaining: tuple[SegmentShift, ...]  # Adtributor's succinct set, most surprising first
    opposing: tuple[SegmentShift, ...]  # segments that moved against the total (EP < 0)
    divergence: float
    explained: float | None  # cumulative EP of the set
    previous_total: float
    current_total: float
    p_value: float


def distribution_shift(
    previous: dict[str, float],
    current: dict[str, float],
    history_divergences: list[float],
    *,
    t_eep: float = 0.1,
    t_ep: float = 0.67,
    min_share_move: float = 0.02,
    max_set: int = 5,
) -> Shift | None:
    """Which segments explain a window-over-window change, and is the mix change unusual?

    With a real total change the set is Adtributor's: segments in descending
    surprise, each carrying at least ``t_eep`` of the change, until the set
    explains ``t_ep``. With (almost) no total change there is no change to
    explain, only a mix shift: the set is the most surprising segments whose
    share moved by ``min_share_move`` or more.
    """
    keys = sorted(set(previous) | set(current))
    f_total = sum(max(previous.get(k, 0.0), 0.0) for k in keys)
    a_total = sum(max(current.get(k, 0.0), 0.0) for k in keys)
    if f_total <= _EPS or a_total <= _EPS or len(keys) < 2:
        return None
    delta = a_total - f_total
    has_change = abs(delta) > 0.01 * max(f_total, a_total)
    rows: list[SegmentShift] = []
    for k in keys:
        f, a = max(previous.get(k, 0.0), 0.0), max(current.get(k, 0.0), 0.0)
        p, q = f / f_total, a / a_total
        rows.append(SegmentShift(
            segment=k, previous=f, current=a, previous_share=p, current_share=q,
            explanatory_power=((a - f) / delta) if has_change else None, surprise=js_term(p, q),
        ))
    rows.sort(key=lambda r: -r.surprise)
    picked: list[SegmentShift] = []
    explained = 0.0
    if has_change:
        for r in rows:
            if r.explanatory_power is not None and r.explanatory_power > t_eep:
                picked.append(r)
                explained += r.explanatory_power
                if explained > t_ep or len(picked) >= max_set:
                    break
        if explained <= t_ep:
            # Adtributor: a dimension whose succinct set cannot explain the change
            # does not localise it.
            return None
    else:
        picked = [r for r in rows if abs(r.current_share - r.previous_share) >= min_share_move][:max_set]
        if not picked:
            return None
    divergence = js_divergence(previous, current) or 0.0
    pred = prediction_t(math.log(divergence + 1e-9), [math.log(h + 1e-9) for h in history_divergences])
    if pred is None:
        return None
    opposing = tuple(r for r in rows if r.explanatory_power is not None and r.explanatory_power < -0.1)
    return Shift(
        explaining=tuple(picked), opposing=opposing[:3], divergence=divergence,
        explained=explained if has_change else None, previous_total=f_total, current_total=a_total,
        p_value=t_tail(pred[0], pred[1], two_sided=False),
    )


# --------------------------------------------------------------------------- co-movement
def co_movement(a: Series, b: Series, min_days: int = 20) -> tuple[float, float, int] | None:
    """(r, p, n) of day-over-day changes after day-of-week adjustment.

    Differencing removes shared trends, the most common source of spurious
    correlation between business series; what remains is "when one moves on a
    given day, does the other move with it". An association, never a cause."""
    days = sorted(d for d in set(a) & set(b) if math.isfinite(a[d]) and math.isfinite(b[d]))
    if len(days) < min_days + 1:
        return None

    def adjusted(s: Series) -> np.ndarray:
        med = {k: float(np.median([s[d] for d in days if d.weekday() == k] or [0.0])) for k in range(7)}
        return np.array([s[d] - med[d.weekday()] for d in days])

    da, db = np.diff(adjusted(a)), np.diff(adjusted(b))
    if float(np.std(da)) <= _EPS or float(np.std(db)) <= _EPS:
        return None
    r = float(np.corrcoef(da, db)[0, 1])
    n = len(da)
    if not math.isfinite(r) or n < 4:
        return None
    r = max(min(r, 0.999999), -0.999999)
    # Bartlett: autocorrelated series carry fewer independent points than days.
    # Differencing makes each series MA(1) (lag-1 autocorrelation near -0.5), so
    # the naive n overstates the evidence and the test ran anti-conservative.
    inflation = 1.0 + 2.0 * sum(_autocorr(da, k) * _autocorr(db, k) for k in range(1, 4))
    n_eff = max(4.0, n / max(inflation, 1.0))
    z = math.atanh(r) * math.sqrt(n_eff - 3)
    return r, two_sided_p(z), n


def _autocorr(x: np.ndarray, lag: int) -> float:
    if len(x) <= lag + 2:
        return 0.0
    c = float(np.corrcoef(x[:-lag], x[lag:])[0, 1])
    return c if math.isfinite(c) else 0.0


# --------------------------------------------------------------------------- multiple testing
def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """BH-adjusted q-values, in the input order."""
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    q = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, p_values[i] * m / rank)
        q[i] = min(1.0, running)
    return q

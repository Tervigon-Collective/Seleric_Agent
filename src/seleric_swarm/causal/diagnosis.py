"""Diagnosis engine — why did a metric change, by how much, and how sure are we?

Pure calculation over already-fetched daily series (no fetching, no catalogue
calls): ``toolsets/diagnosis.py`` plans and fetches, this module reasons. Four
layers, each with its own standard of evidence, reported separately so an
arithmetic decomposition is never dressed up as a behavioural cause:

1. **Event** — is the change real? The event is compared with the same weekday
   in recent weeks (outlier weeks skipped), and its size is judged against a
   robust day-of-week + trend fit of the history. A change inside normal
   variation is reported as such; the engine does not invent a cause for noise.
2. **Decomposition (what changed)** — exact accounting identities. Candidates
   come from catalogue lineage (``formula.depends_on``, both directions) and are
   accepted only when the data verify them day by day (``Y = k * prod(X_i^e_i)``
   with a constant ``k``). The change is split with Shapley values, which are
   exact and order-independent.
3. **Localisation (where it changed)** — segment decomposition over catalogue
   dimensions. Additive metrics split exactly by segment; a rate splits into a
   mix effect and a within-segment rate effect, which is where Simpson's paradox
   shows up. Each dimension's segment-specificity is compared with the
   specificity a normal day shows, so a noisy high-cardinality dimension does not
   look like a finding.
4. **Drivers (why it changed)** — upstream candidates estimated with DoWhy on an
   explicit time-series DAG (``dowhy_service.estimate_time_series_effect``):
   calendar and lagged pre-treatment variables only, HAC intervals, refuters plus
   temporal-precedence tests. Contribution = effect x how far the driver itself
   moved from its own reference. Classification is rule-based and conservative:

   - ``supported_cause``: identified effect whose CI excludes 0, every refuter
     passes, the driver moved at the event in the direction that explains it,
     and past driver values predict the outcome while the reverse does not.
   - ``likely_contributor``: same, but direction rests on an assumption (same-day
     association only) or a non-placebo robustness check failed.
   - ``correlation``: associated, but the adjusted effect is not distinguishable
     from 0, a placebo refuter fails, or the outcome feeds back into the driver.
   - ``insufficient_evidence``: the data cannot support an estimate.

   Decomposition terms are exact identities and carry ``basis="identity"``;
   they say *what* moved, not *why*.

Nothing here names a metric, dimension, value or business threshold.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations, permutations
from typing import Any, Literal

from seleric_swarm.causal.dowhy_service import TimeSeriesEffect, estimate_time_series_effect
from seleric_swarm.toolsets import policy_config as P

_log = logging.getLogger(__name__)

Series = dict[date, float]
Classification = Literal["supported_cause", "likely_contributor", "correlation", "insufficient_evidence"]
Direction = Literal["down", "up"]


# --------------------------------------------------------------------------- metadata
@dataclass(frozen=True)
class MetricMeta:
    """The lineage facts the engine needs about one catalogue metric."""

    id: str
    aggregation: str = ""
    view: str = ""
    depends_on: tuple[str, ...] = ()
    label: str = ""
    grain: str = ""
    unit: str = ""

    @property
    def additive(self) -> bool:
        return self.aggregation == "additive"

    def entity_tokens(self, scope_tokens: frozenset[str] = frozenset()) -> frozenset[str]:
        """The units a metric is counted on, from its catalogue grain.

        ``brand_order_day_channel_ad`` -> {order, channel, ad}: calendar words and
        scope keys (the tenant dimension every view carries) are not units.
        """
        tokens = {t for t in self.grain.lower().replace("-", "_").split("_") if t}
        return frozenset(tokens - set(P.DIAG_CALENDAR_GRAIN_TOKENS) - scope_tokens)


def lineage_from_definitions(definitions: dict[str, dict[str, Any]]) -> dict[str, MetricMeta]:
    """Catalogue definitions (``catalogue_get_metric(s)``) -> lineage map."""
    out: dict[str, MetricMeta] = {}
    for mid, d in definitions.items():
        if not isinstance(d, dict):
            continue
        formula = d.get("formula") if isinstance(d.get("formula"), dict) else {}
        view = (d.get("cube_mapping") or {}).get("view") or d.get("view") or ""
        out[mid] = MetricMeta(
            id=mid,
            aggregation=str(d.get("aggregation") or "").lower(),
            view=str(view),
            depends_on=tuple(str(x) for x in (formula.get("depends_on") or []) if x),
            label=str(d.get("display_name") or d.get("label") or mid),
            grain=str(d.get("grain") or ""),
            unit=str(d.get("unit") or d.get("currency_default") or ""),
        )
    return out


def descendants(metric_id: str, lineage: dict[str, MetricMeta]) -> set[str]:
    """Metrics computed (directly or transitively) from ``metric_id``."""
    children: dict[str, set[str]] = {}
    for m in lineage.values():
        for dep in m.depends_on:
            children.setdefault(dep, set()).add(m.id)
    seen: set[str] = set()
    stack = [metric_id]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    seen.discard(metric_id)
    return seen


def lineage_in_degree(lineage: dict[str, MetricMeta]) -> dict[str, int]:
    """How many catalogue metrics are built on each metric (a hub score)."""
    deg: dict[str, int] = {}
    for m in lineage.values():
        for dep in m.depends_on:
            deg[dep] = deg.get(dep, 0) + 1
    return deg


# --------------------------------------------------------------------------- helpers
def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return math.nan
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else math.nan


def _sign(x: float) -> int:
    return 0 if abs(x) < 1e-12 else (1 if x > 0 else -1)


def _rel(delta: float, base: float) -> float | None:
    return delta / abs(base) if abs(base) > 1e-12 else None


# --------------------------------------------------------------------------- calendar model
@dataclass
class CalendarFit:
    """Robust (Huber) fit of ``y ~ trend + day-of-week`` over history days."""

    params: list[float]
    scale: float
    t0: date
    has_dow: bool
    n: int
    residual_z: dict[date, float] = field(default_factory=dict)

    def _row(self, d: date) -> list[float]:
        row = [1.0, (d - self.t0).days / 7.0]
        if self.has_dow:
            row += [1.0 if d.weekday() == k else 0.0 for k in range(1, 7)]
        return row

    def predict(self, d: date) -> float:
        return sum(p * x for p, x in zip(self.params, self._row(d), strict=True))


def fit_calendar(series: Series, days: list[date]) -> CalendarFit | None:
    pts = [(d, float(series[d])) for d in sorted(days) if d in series and _finite(series[d])]
    if len(pts) < P.MIN_OBSERVATION_ROWS:
        return None
    import numpy as np

    t0 = pts[0][0]
    has_dow = (pts[-1][0] - t0).days + 1 >= 21 and len(pts) >= 14
    fit = CalendarFit(params=[], scale=0.0, t0=t0, has_dow=has_dow, n=len(pts))
    X = np.array([fit._row(d) for d, _ in pts])
    y = np.array([v for _, v in pts])

    def robust(Xs: Any, ys: Any) -> tuple[list[float], float]:
        try:
            import statsmodels.api as sm

            res = sm.RLM(ys, Xs, M=sm.robust.norms.HuberT()).fit()
            params, scale = [float(p) for p in res.params], float(res.scale)
        except Exception:
            params, scale = [float(p) for p in np.linalg.lstsq(Xs, ys, rcond=None)[0]], 0.0
        r = ys - Xs @ np.array(params)
        if not scale or not math.isfinite(scale):
            mad = _median([abs(v - _median(list(r))) for v in r]) * 1.4826
            scale = mad if mad > 0 else float(np.std(r)) or 1e-9
        return params, scale

    # Fit, drop the days it flags as outliers, refit — up to three rounds. A long
    # abnormal stretch (an outage week) is ~15-20% of a two-month history and
    # still inflates a single Huber pass enough to hide a real event.
    keep = np.ones(len(y), dtype=bool)
    params, scale = robust(X, y)
    for _ in range(3):
        z = (y - X @ np.array(params)) / scale
        new_keep = np.abs(z) < P.DIAG_OUTLIER_Z
        if new_keep.sum() < max(P.MIN_OBSERVATION_ROWS, X.shape[1] + 3) or (new_keep == keep).all():
            break
        keep = new_keep
        params, scale = robust(X[keep], y[keep])
    fit.params = params
    fit.scale = scale
    resid = y - X @ np.array(params)
    fit.residual_z = {d: float(r / scale) for (d, _), r in zip(pts, resid, strict=True)}
    return fit


# --------------------------------------------------------------------------- identities
@dataclass
class Identity:
    outcome: str
    factors: list[tuple[str, float]]  # (metric_id, exponent)
    k: float
    dispersion: float
    n_days: int
    approximate: bool = False

    def value(self, xs: list[float]) -> float:
        out = self.k
        for (_, e), x in zip(self.factors, xs, strict=True):
            if x == 0 and e < 0:
                return math.nan
            out *= x**e
        return out

    def describe(self) -> str:
        num = [m for m, e in self.factors if e > 0]
        den = [m for m, e in self.factors if e < 0]
        rhs = " × ".join(num) or "1"
        if den:
            rhs += " ÷ " + " ÷ ".join(den)
        if not math.isclose(self.k, 1.0, rel_tol=1e-3):
            rhs = f"{self.k:.6g} × " + rhs
        return f"{self.outcome} {'≈' if self.approximate else '='} {rhs}"


def _verify(
    outcome: str, factors: list[tuple[str, float]], series: dict[str, Series], days: list[date],
    *, tolerance: float | None = None,
) -> Identity | None:
    tol = P.DIAG_IDENTITY_TOLERANCE if tolerance is None else tolerance
    ks: list[float] = []
    for d in days:
        y = series.get(outcome, {}).get(d)
        xs = [series.get(m, {}).get(d) for m, _ in factors]
        if not _finite(y) or not all(_finite(x) for x in xs):
            continue
        prod = 1.0
        ok = True
        for (_, e), x in zip(factors, xs, strict=True):
            if x <= 0:
                ok = False
                break
            prod *= float(x) ** e
        if not ok or y == 0 or prod == 0:
            continue
        ks.append(float(y) / prod)
    if len(ks) < P.MIN_OBSERVATION_ROWS:
        return None
    k = _median(ks)
    if k <= 0 or not math.isfinite(k):
        return None
    dispersion = _median([abs(v - k) for v in ks]) / abs(k)
    worst = max(abs(v - k) for v in ks) / abs(k)
    # The median deviation must be tiny and no day may break the identity badly.
    if dispersion > tol or (tolerance is None and worst > 10 * tol):
        return None
    return Identity(
        outcome=outcome, factors=factors, k=k, dispersion=dispersion, n_days=len(ks),
        approximate=dispersion > P.DIAG_IDENTITY_TOLERANCE,
    )


def rate_chain_proposals(metric: str, lineage: dict[str, MetricMeta]) -> list[tuple[str, str]]:
    """(rate, base) pairs that could rebuild an additive ``metric`` as rate x base.

    Lineage only: a ratio whose ``depends_on`` is a single additive base is a
    "per base" rate (conversions per session, completions per impression, ...).
    Whether ``metric`` really equals rate x base is for the data to say.
    """
    out: list[tuple[str, str]] = []
    for r in lineage.values():
        if r.additive or r.id == metric or len(r.depends_on) != 1:
            continue
        base = r.depends_on[0]
        if base == metric or not lineage.get(base, MetricMeta(base)).additive:
            continue
        out.append((r.id, base))
    return out


def discover_rate_chains(
    metric: str, lineage: dict[str, MetricMeta], series: dict[str, Series], days: list[date]
) -> list[Identity]:
    """Data-verified approximate identities ``metric ≈ k x rate x base``.

    Two systems counting the same events (an order ledger and a web tracker)
    agree only approximately, so these are accepted at a looser tolerance and
    always reported as approximate.
    """
    found: list[Identity] = []
    for rate, base in rate_chain_proposals(metric, lineage):
        if rate not in series or base not in series:
            continue
        # rate x base counts base units that did something; that is only the
        # same events as ``metric`` if the rate is a share and the two counts
        # agree in scale (two systems counting one thing differ by less than 2x).
        # Two merely co-moving volumes (spend vs viewing sessions) fail this.
        rate_vals = [float(series[rate][d]) for d in days if _finite(series[rate].get(d))]
        if not rate_vals or min(rate_vals) < 0 or max(rate_vals) > 1:
            continue
        ident = _verify(metric, [(rate, 1.0), (base, 1.0)], series, days, tolerance=P.DIAG_APPROX_IDENTITY_TOLERANCE)
        if ident is not None and 0.5 <= ident.k <= 2.0:
            ident.approximate = True
            found.append(ident)
    return sorted(found, key=lambda i: i.dispersion)


def discover_identities(
    outcome: str, lineage: dict[str, MetricMeta], series: dict[str, Series], days: list[date]
) -> list[Identity]:
    """Lineage-proposed, data-verified product/ratio identities for ``outcome``."""
    proposals: list[list[tuple[str, float]]] = []
    meta = lineage.get(outcome)
    if meta is not None:
        deps = [d for d in meta.depends_on if d in series and d != outcome]
        for a, b in permutations(deps, 2):
            proposals.append([(a, 1.0), (b, -1.0)])
        for a, b in combinations(deps, 2):
            proposals.append([(a, 1.0), (b, 1.0)])
    for r in lineage.values():
        if r.id == outcome or outcome not in r.depends_on or r.id not in series:
            continue
        for other in r.depends_on:
            if other in (outcome, r.id) or other not in series:
                continue
            proposals.append([(r.id, 1.0), (other, 1.0)])  # r = Y / other
            proposals.append([(other, 1.0), (r.id, -1.0)])  # r = other / Y
    desc = descendants(outcome, lineage)
    found: dict[frozenset[tuple[str, float]], Identity] = {}
    for factors in proposals:
        derived = [m for m, _ in factors if m in desc]
        others = [m for m, _ in factors if m not in desc]
        # A factor computed from Y is only informative as a per-unit rate next to
        # additive volumes (sales = volume x value per unit); "Y = ltv / (ltv / Y)"
        # is true but explains nothing.
        if derived and (len(derived) > 1 or not all(lineage.get(m, MetricMeta(m)).additive for m in others)):
            continue
        key = frozenset(factors)
        if key in found:
            continue
        ident = _verify(outcome, factors, series, days)
        if ident is not None:
            found[key] = ident
    # Forward identities (built only from Y's own components) first.
    return sorted(found.values(), key=lambda i: (any(m in desc for m, _ in i.factors), i.dispersion, len(i.factors)))


def shapley(f: Any, x0: list[float], x1: list[float]) -> list[float]:
    """Exact Shapley attribution of ``f(x1) - f(x0)`` to each input."""
    n = len(x0)
    out = [0.0] * n
    for i in range(n):
        others = [j for j in range(n) if j != i]
        for r in range(len(others) + 1):
            w = math.factorial(r) * math.factorial(n - r - 1) / math.factorial(n)
            for subset in combinations(others, r):
                base = [x1[j] if j in subset else x0[j] for j in range(n)]
                with_i = list(base)
                with_i[i] = x1[i]
                out[i] += w * (f(with_i) - f(base))
    return out


# --------------------------------------------------------------------------- seasonal-naive profile
def _prior_days(d: date, pool: set[date], k: int, exclude: set[date], same_weekday: bool) -> list[date]:
    if same_weekday:
        cands = [d - timedelta(days=7 * j) for j in range(1, 3 * k + 1)]
    else:
        cands = [d - timedelta(days=j) for j in range(1, 3 * k + 8)]
    return [c for c in cands if c in pool and c not in exclude][: (k if same_weekday else max(k, 7))]


def _robust_center_scale(xs: list[float]) -> tuple[float, float]:
    c = _median(xs)
    mad = _median([abs(x - c) for x in xs]) * 1.4826
    if mad <= 0:
        sd = math.sqrt(_mean([(x - c) ** 2 for x in xs])) if xs else 0.0
        mad = sd if sd > 0 else 1e-9
    return c, mad


_LEVEL_DAYS = 7
_LEVEL_FACTOR_BOUNDS = (0.2, 5.0)


class Referencer:
    """Level-adjusted same-weekday references for one series.

    ``reference(d) = mean over recent same-weekday normal days r of
    y(r) x level(before d) / level(before r)``, where a level is the mean of the
    last seven normal days. The weekday pattern comes from the same-weekday
    days; the level ratio carries a recent regime change (a budget cut, a
    post-outage recovery) instead of averaging two regimes together. Seven-day
    levels span whole weeks, so the ratio itself is free of weekday effects.
    """

    def __init__(self, series: Series, history: list[date], outliers: set[date], same_weekday: bool, weeks: int) -> None:
        self.series = series
        self.pool = {d for d in history if _finite(series.get(d))}
        self.outliers = outliers
        self.same_weekday = same_weekday
        self.weeks = weeks
        self._levels: dict[date, float | None] = {}

    def level_before(self, d: date) -> float | None:
        if d in self._levels:
            return self._levels[d]
        # Only the seven calendar days just before d: reaching further back past
        # an abnormal stretch would date the level to a different regime.
        vals = [
            float(self.series[x])
            for x in (d - timedelta(days=j) for j in range(1, _LEVEL_DAYS + 1))
            if x in self.pool and x not in self.outliers
        ]
        lvl = _mean(vals) if len(vals) >= 4 else None
        self._levels[d] = lvl
        return lvl

    def refs(self, d: date) -> list[date]:
        return _prior_days(d, self.pool, self.weeks, self.outliers, self.same_weekday)

    def trimmed(self, anchor: date, refs: list[date]) -> tuple[list[date], dict[date, float]]:
        """References with the highest and lowest level-adjusted value dropped
        (when four or more remain), plus their factors. Used for the outcome AND
        its segments/components, so references stay exactly additive. A
        reference day with no valid recent level is dropped when at least two
        others have one (its scaling would be guesswork)."""
        if self.level_before(anchor) is not None:
            known = [r for r in refs if self.level_before(r) is not None]
            if len(known) >= 2:
                refs = known
        f = self.factors(anchor, refs)
        if len(refs) >= 4:
            ranked = sorted(refs, key=lambda r: float(self.series[r]) * f[r])
            refs = [r for r in refs if r not in (ranked[0], ranked[-1])]
        return refs, {r: f[r] for r in refs}

    def factors(self, anchor: date, refs: list[date]) -> dict[date, float]:
        """Level ratio per reference day; 1.0 for all when any level is unknown."""
        now = self.level_before(anchor)
        out: dict[date, float] = {}
        for r in refs:
            then = self.level_before(r)
            if now is None or then is None or then <= 0 or now < 0:
                return {r: 1.0 for r in refs}
            lo, hi = _LEVEL_FACTOR_BOUNDS
            out[r] = min(hi, max(lo, now / then))
        return out


def _transform(series: Series, pool: set[date]) -> tuple[bool, float]:
    """Log scale for non-negative series (multiplicative noise; drops are bounded at
    -100% while rises are not), with a small offset so zeros stay finite."""
    vals = [float(series[d]) for d in pool]
    if not vals or min(vals) < 0:
        return False, 0.0
    med = _median([abs(v) for v in vals])
    return True, (0.01 * med if med > 0 else 1.0)


def _err(actual: float, ref: float, use_log: bool, offset: float) -> float | None:
    if use_log:
        return math.log(actual + offset) - math.log(ref + offset) if ref + offset > 0 and actual + offset > 0 else None
    return (actual - ref) / abs(ref) if abs(ref) > 1e-12 else None


@dataclass
class SeasonalProfile:
    """How far a day (or an n-day window) normally sits from its own reference.

    Errors are taken on a log scale (relative when the series can go
    negative); the centre/scale are median/MAD over windows that touch no
    abnormal day, so an outage or a sale cannot widen "normal" until it hides.
    """

    same_weekday: bool
    outliers: set[date]
    errors: list[float]
    center: float
    scale: float
    day_center: float
    day_scale: float
    use_log: bool
    offset: float
    referencer: Referencer

    def z(self, err: float) -> float:
        return (err - self.center) / self.scale

    def day_z(self, err: float) -> float:
        return (err - self.day_center) / self.day_scale

    def percentile(self, err: float) -> float:
        return sum(1 for e in self.errors if e <= err) / len(self.errors) if self.errors else math.nan

    def window_error(self, days: list[date], *, additive: bool, actual: float | None = None) -> tuple[float | None, float | None, float | None]:
        """(error, actual, reference) of a window against level-adjusted references."""
        ref = self.referencer
        anchor = min(days)
        vals: list[float] = []
        refs_v: list[float] = []
        for d in days:
            rr = ref.refs(d)
            if len(rr) < 2:
                return None, None, None
            rr, f = ref.trimmed(anchor, rr)
            refs_v.append(_mean([float(ref.series[r]) * f[r] for r in rr]))
            if actual is None:
                if not _finite(ref.series.get(d)):
                    return None, None, None
                vals.append(float(ref.series[d]))
        n = len(days)
        rf = sum(refs_v) if additive else _mean(refs_v)
        ev = actual if actual is not None else (sum(vals) if additive else _mean(vals))
        scale_off = self.offset * (n if additive else 1)
        return _err(ev, rf, self.use_log, scale_off), ev, rf


def seasonal_profile(
    series: Series, history: list[date], n_window: int, *, additive: bool, weeks: int = 0,
) -> SeasonalProfile | None:
    weeks = weeks or P.DIAG_REFERENCE_WEEKS
    pool = {d for d in history if _finite(series.get(d))}
    if len(pool) < P.MIN_OBSERVATION_ROWS:
        return None
    same_weekday = (max(pool) - min(pool)).days + 1 >= 21
    use_log, offset = _transform(series, pool)
    # Seed: deviation from the centred median of the same weekday ±3 weeks
    # (prior-only references are contaminated for weeks after an outage).
    step = 7 if same_weekday else 1
    seed: dict[date, float] = {}
    for h in pool:
        around = [h + timedelta(days=step * j) for j in range(-3, 4)]
        vals = [float(series[a]) for a in around if a in pool]
        if len(vals) >= 3 and (e := _err(float(series[h]), _median(vals), use_log, offset)) is not None:
            seed[h] = e
    outliers: set[date] = set()
    if len(seed) >= P.MIN_OBSERVATION_ROWS:
        c0, s0 = _robust_center_scale(list(seed.values()))
        outliers = {h for h, e in seed.items() if abs(e - c0) / s0 >= P.DIAG_OUTLIER_Z}
    day_errors: dict[date, float] = {}
    referencer = Referencer(series, history, outliers, same_weekday, weeks)
    for _ in range(6):  # re-reference without abnormal days, re-detect on normal-day scale
        referencer = Referencer(series, history, outliers, same_weekday, weeks)
        day_errors = {}
        for h in sorted(pool):
            rr = referencer.refs(h)
            if len(rr) < 2:
                continue
            f = referencer.factors(h, rr)
            e = _err(float(series[h]), _median([float(series[r]) * f[r] for r in rr]), use_log, offset)
            if e is not None:
                day_errors[h] = e
        if len(day_errors) < P.MIN_OBSERVATION_ROWS:
            return None
        # Scale from the days currently believed normal, so a long abnormal
        # stretch cannot widen "normal" until it hides itself.
        normal = [e for h, e in day_errors.items() if h not in outliers] or list(day_errors.values())
        c, sc = _robust_center_scale(normal)
        new = {h for h, e in day_errors.items() if abs(e - c) / sc >= P.DIAG_OUTLIER_Z}
        # A flagged day this pass cannot re-evaluate (no earlier reference) keeps
        # its flag; dropping it would re-contaminate every later reference.
        new |= {h for h in outliers if h not in day_errors}
        if new == outliers:
            break
        outliers = new
    referencer = Referencer(series, history, outliers, same_weekday, weeks)
    day_center, day_scale = _robust_center_scale([e for h, e in day_errors.items() if h not in outliers])
    prof = SeasonalProfile(same_weekday, outliers, [], 0.0, 1.0, day_center, day_scale, use_log, offset, referencer)
    errors: list[float] = []
    for h in sorted(pool):
        days = [h - timedelta(days=i) for i in range(n_window - 1, -1, -1)]
        if not all(d in pool and d not in outliers for d in days):
            continue
        e, _, _ = prof.window_error(days, additive=additive)
        if e is not None:
            errors.append(e)
    if len(errors) < P.MIN_OBSERVATION_ROWS:
        return None
    prof.errors = errors
    prof.center, prof.scale = _robust_center_scale(errors)
    return prof


def _abs_noise(series: Series, history: list[date], exclude: set[date], same_weekday: bool) -> float | None:
    """Robust absolute day noise of a (segment) series around its own reference."""
    pool = set(history)
    errs: list[float] = []
    for h in history:
        if h in exclude:
            continue
        refs = _prior_days(h, pool, P.DIAG_REFERENCE_WEEKS, exclude, same_weekday)
        if len(refs) < 2:
            continue
        errs.append(float(series.get(h, 0.0)) - _mean([float(series.get(r, 0.0)) for r in refs]))
    if len(errs) < P.MIN_OBSERVATION_ROWS:
        return None
    _, sc = _robust_center_scale(errs)
    return sc if sc > 1e-9 else None


def _lockstep_correlation(a: Series, b: Series, history: list[date], exclude: set[date], same_weekday: bool) -> float | None:
    """Correlation of two series' relative deviations from their own references on NORMAL days.

    Two measurements of the same units move together even on ordinary days;
    a behavioural driver and its outcome share a shock mostly on abnormal days.
    """
    pool = set(history)
    xa: list[float] = []
    xb: list[float] = []
    pa = {d for d in pool if _finite(a.get(d))}
    pb = {d for d in pool if _finite(b.get(d))}
    for h in history:
        if h in exclude or not (_finite(a.get(h)) and _finite(b.get(h))):
            continue
        ra = _prior_days(h, pa, P.DIAG_REFERENCE_WEEKS, exclude, same_weekday)
        rb = _prior_days(h, pb, P.DIAG_REFERENCE_WEEKS, exclude, same_weekday)
        if len(ra) < 2 or len(rb) < 2:
            continue
        ma, mb = _mean([float(a[r]) for r in ra]), _mean([float(b[r]) for r in rb])
        if abs(ma) < 1e-12 or abs(mb) < 1e-12:
            continue
        xa.append((float(a[h]) - ma) / abs(ma))
        xb.append((float(b[h]) - mb) / abs(mb))
    if len(xa) < P.MIN_OBSERVATION_ROWS:
        return None
    m1, m2 = _mean(xa), _mean(xb)
    s1 = math.sqrt(sum((x - m1) ** 2 for x in xa))
    s2 = math.sqrt(sum((x - m2) ** 2 for x in xb))
    if s1 == 0 or s2 == 0:
        return None
    return sum((x - m1) * (y - m2) for x, y in zip(xa, xb, strict=True)) / (s1 * s2)


# --------------------------------------------------------------------------- event window
@dataclass
class EventWindow:
    event_days: list[date]
    history_days: list[date]  # complete days before the event (estimation sample)
    reference: dict[date, list[date]]  # event day -> same-weekday reference days
    excluded_outliers: list[date]
    reference_kind: str
    # event day -> reference day -> level factor, taken from the OUTCOME and shared
    # by its segments and components so references stay exactly additive.
    factors: dict[date, dict[date, float]] = field(default_factory=dict)

    def factor(self, d: date, r: date) -> float:
        return self.factors.get(d, {}).get(r, 1.0)


def _ref_value(series: Series, refs: list[date], factors: dict[date, float] | None = None) -> float | None:
    vals = [float(series[r]) * (factors or {}).get(r, 1.0) for r in refs if r in series and _finite(series[r])]
    return _mean(vals) if vals else None


def _additive_window(series: Series, window: EventWindow, *, missing_as_zero: bool) -> tuple[float | None, float | None]:
    """(event sum, reference sum) of an additive series."""
    ev = 0.0
    rf = 0.0
    for d in window.event_days:
        v = series.get(d)
        if not _finite(v):
            if not missing_as_zero:
                return None, None
            v = 0.0
        ev += float(v)
        vals = [
            (float(series.get(r, 0.0)) if missing_as_zero else float(series[r])) * window.factor(d, r)
            for r in window.reference[d] if (r in series or missing_as_zero)
        ]
        vals = [x for x in vals if math.isfinite(x)]
        if not vals:
            return ev, None
        rf += _mean(vals)
    return ev, rf


def _daily_window(series: Series, window: EventWindow) -> tuple[float | None, float | None]:
    """(event mean, reference mean) of a non-additive daily series."""
    ev = [float(series[d]) for d in window.event_days if d in series and _finite(series[d])]
    if len(ev) != len(window.event_days):
        return None, None
    refs = [_ref_value(series, window.reference[d], window.factors.get(d)) for d in window.event_days]
    if any(r is None for r in refs):
        return _mean(ev), None
    return _mean(ev), _mean([r for r in refs if r is not None])


def reference_days(
    event_days: list[date], history: list[date], outliers: set[date], weeks: int
) -> tuple[dict[date, list[date]], str]:
    hist = set(history)
    ref: dict[date, list[date]] = {}
    kind = "same weekday"
    for d in event_days:
        picks = [
            d - timedelta(days=7 * k)
            for k in range(1, 3 * weeks + 1)
            if (d - timedelta(days=7 * k)) in hist and (d - timedelta(days=7 * k)) not in outliers
        ][:weeks]
        ref[d] = picks
    if any(len(v) < 2 for v in ref.values()):
        # Not enough same-weekday history: fall back to the nearest normal days.
        kind = "nearest normal days (too little same-weekday history)"
        normal = sorted((h for h in hist if h not in outliers), reverse=True)
        for d in event_days:
            if len(ref[d]) < 2:
                ref[d] = [h for h in normal if h < min(event_days)][: max(weeks, 2)]
    return ref, kind


# --------------------------------------------------------------------------- report types
@dataclass
class EventSummary:
    metric: str
    actual: float | None
    reference: float | None
    delta: float | None
    delta_pct: float | None
    expected_by_model: float | None
    z_score: float | None
    unusual: bool
    direction: Direction | None
    # vs_previous_only: the asked direction holds against the day before but not
    # against the usual for that weekday (live 2026-10-05 MS3-fbcf78e410: "why did
    # sales fall yesterday" -> "did not fall, it rose" while sales were 25% below
    # the previous day).
    premise: Literal["confirmed", "contradicted", "not_unusual", "not_stated", "vs_previous_only"]
    strength: Literal["strong", "moderate", "none"]
    previous_period: dict[str, Any]
    reference_kind: str
    reference_days: dict[str, list[str]]
    excluded_outlier_days: list[str]
    aggregation_note: str = ""
    sampling: dict[str, Any] = field(default_factory=dict)
    level_factors: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class DecompositionTerm:
    metric: str
    exponent: float
    event: float
    reference: float
    contribution: float
    share_of_change: float | None
    basis: str = "identity"


@dataclass
class SegmentMove:
    segment: str
    event: float
    reference: float
    delta: float
    share_of_change: float | None
    rate_event: float | None = None
    rate_reference: float | None = None
    rate_effect: float | None = None
    mix_effect: float | None = None
    z_score: float | None = None
    significant: bool | None = None


@dataclass
class DimensionFinding:
    dimension: str
    kind: Literal["additive", "rate"]
    n_segments: int
    coverage: float | None
    specificity: float | None
    typical_specificity: float | None
    localised: bool
    broad_based: bool
    top: list[SegmentMove]
    mix_effect: float | None = None
    rate_effect: float | None = None
    simpsons_paradox: bool = False
    note: str = ""
    volume_coverage: float | None = None  # share of the outcome's reference the segments add up to
    hot_share: float | None = None  # share of the change carried by segments beyond their own noise


@dataclass
class DriverFinding:
    driver: str
    classification: Classification | None
    status: Literal["implicated", "ruled_out", "excluded", "insufficient_evidence"]
    reason: str
    effect_per_unit: float | None = None
    effect_ci: list[float] = field(default_factory=list)
    p_value: float | None = None
    driver_event: float | None = None
    driver_reference: float | None = None
    driver_delta: float | None = None
    driver_z: float | None = None
    driver_moved_before_or_at_event: bool | None = None
    contribution: float | None = None
    contribution_ci: list[float] = field(default_factory=list)
    share_of_change: float | None = None
    causal_path: str = ""
    adjustment_set: list[str] = field(default_factory=list)
    graph_edges: list[list[str]] = field(default_factory=list)  # the DAG DoWhy identified the effect on
    identified_estimand: str = ""
    direction_evidence: dict[str, Any] = field(default_factory=dict)
    refutations: list[dict[str, Any]] = field(default_factory=list)
    naive_association: dict[str, Any] = field(default_factory=dict)
    inseparable_from: list[str] = field(default_factory=list)
    ruled_out_because: Literal["", "no_effect", "did_not_move", "wrong_direction"] = ""
    n_rows: int = 0
    estimator: str = ""
    assumptions: list[str] = field(default_factory=list)


@dataclass
class DiagnosisReport:
    outcome: str
    event_window: list[str]
    verdict: Literal[
        "no_unusual_change", "explained", "partially_explained", "located_cause_not_identified",
        "root_cause_not_identified", "insufficient_data",
    ]
    headline: str
    event: EventSummary | None
    identities: list[str] = field(default_factory=list)
    decomposition: list[DecompositionTerm] = field(default_factory=list)
    # Next link down: the dominant additive component rebuilt as rate x base
    # (approximate, data-verified), and where that rate moved.
    chain_identity: str = ""
    chain: list[DecompositionTerm] = field(default_factory=list)
    chain_dimensions: list[DimensionFinding] = field(default_factory=list)
    dimensions: list[DimensionFinding] = field(default_factory=list)
    drivers: list[DriverFinding] = field(default_factory=list)
    competing_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    data_quality: list[str] = field(default_factory=list)
    unexplained_share: float | None = None
    # Plain-language answer skeleton: every required part, catalogue labels, no ids.
    narrative: list[str] = field(default_factory=list)
    # The day(s) just before the event, for every metric the report cites.
    previous_values: dict[str, float] = field(default_factory=dict)
    # The same metrics on the event day(s). Without them an answer tabulating
    # "yesterday vs the day before" had the day before for every driver and
    # printed "No data available" for yesterday (live 2026-10-05 MS3-5775d28dfd).
    event_values: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


# --------------------------------------------------------------------------- inputs
@dataclass
class DiagnosisInput:
    outcome: str
    event_days: list[date]
    series: dict[str, Series]
    lineage: dict[str, MetricMeta]
    # metric -> dimension -> segment -> day -> value
    segments: dict[str, dict[str, dict[str, Series]]] = field(default_factory=dict)
    candidate_drivers: list[str] = field(default_factory=list)
    partial_days: set[date] = field(default_factory=set)
    claimed_direction: Direction | None = None
    denominators: dict[str, str] = field(default_factory=dict)  # rate metric -> weight metric
    scope_tokens: frozenset[str] = frozenset()  # grain tokens naming the tenant scope, not a unit


# --------------------------------------------------------------------------- engine
def _event_value(
    metric: str, inp: DiagnosisInput, window: EventWindow, identity: Identity | None
) -> tuple[float | None, float | None, str]:
    """(event, reference, note) for a metric, exact whenever lineage allows it."""
    meta = inp.lineage.get(metric)
    series = inp.series.get(metric, {})
    if meta is None or meta.additive:
        ev, rf = _additive_window(series, window, missing_as_zero=False)
        return ev, rf, ""
    if identity is not None and all(inp.lineage.get(m, MetricMeta(m)).additive for m, _ in identity.factors):
        vals = _additive_factor_values(inp, window, identity)
        evs = [vals[m][0] for m, _ in identity.factors]
        rfs = [vals[m][1] for m, _ in identity.factors]
        if all(v is not None for v in (*evs, *rfs)):
            return identity.value(evs), identity.value(rfs), f"recomputed from {identity.describe()}"
    ev, rf = _daily_window(series, window)
    note = "" if len(window.event_days) == 1 else "multi-day rate taken as the mean of daily values (approximate)"
    return ev, rf, note


def _linked_outliers(inp: DiagnosisInput, window: EventWindow, metrics: list[str]) -> set[date]:
    """Abnormal days of the outcome plus every linked metric."""
    out = set(window.excluded_outliers)
    for m in metrics:
        meta = inp.lineage.get(m, MetricMeta(m))
        prof = seasonal_profile(inp.series.get(m, {}), window.history_days, len(window.event_days), additive=meta.additive)
        if prof is not None:
            out |= prof.outliers
    return out


def _additive_factor_values(
    inp: DiagnosisInput, window: EventWindow, identity: Identity, linked: set[date] | None = None,
) -> dict[str, tuple[float | None, float | None]]:
    """(event, reference) per ADDITIVE identity factor, each on its own
    level-adjusted reference with the linked metrics' abnormal days excluded."""
    if linked is None:
        linked = _linked_outliers(inp, window, [m for m, _ in identity.factors])
    out: dict[str, tuple[float | None, float | None]] = {}
    for m, _ in identity.factors:
        if not inp.lineage.get(m, MetricMeta(m)).additive:
            continue
        own, _ = _own_window(
            inp.series.get(m, {}), window.history_days, window.event_days, additive=True,
            kind=window.reference_kind, extra_outliers=linked,
        )
        out[m] = _additive_window(inp.series.get(m, {}), own or window, missing_as_zero=False)
    return out


def _decompose(
    inp: DiagnosisInput, window: EventWindow, identity: Identity, y_ev: float, y_rf: float
) -> list[DecompositionTerm]:
    evs: list[float | None] = []
    rfs: list[float | None] = []
    # Each volume on its OWN level-adjusted reference; the per-unit factor is
    # then solved from the identity, so the split stays exact.
    additive_vals = _additive_factor_values(inp, window, identity)
    for m, _ in identity.factors:
        meta = inp.lineage.get(m, MetricMeta(m))
        if meta.additive:
            e, r = additive_vals[m]
        else:
            e, r = None, None  # solved below from the identity
        evs.append(e)
        rfs.append(r)
    unknown = [i for i, v in enumerate(evs) if v is None]
    if len(unknown) == 1:
        # Solve the one non-additive factor from the identity so the split stays
        # exact (mean of daily ratios != ratio of the summed components).
        i = unknown[0]
        e_i = identity.factors[i][1]

        def solve(y: float, xs: list[float | None]) -> float | None:
            rest = identity.k
            for j, ((_, e), x) in enumerate(zip(identity.factors, xs, strict=True)):
                if j == i:
                    continue
                if x is None or x <= 0:
                    return None
                rest *= x**e
            if rest == 0 or y <= 0:
                return None
            return (y / rest) ** (1.0 / e_i)

        evs[i] = solve(y_ev, evs)
        rfs[i] = solve(y_rf, rfs)
    elif unknown and len(window.event_days) == 1:
        d = window.event_days[0]
        for i in unknown:
            m = identity.factors[i][0]
            evs[i] = inp.series.get(m, {}).get(d)
            rfs[i] = _ref_value(inp.series.get(m, {}), window.reference[d], window.factors.get(d))
    if any(v is None or not _finite(v) for v in (*evs, *rfs)):
        return []
    x0 = [float(v) for v in rfs]  # type: ignore[arg-type]
    x1 = [float(v) for v in evs]  # type: ignore[arg-type]
    try:
        contrib = shapley(identity.value, x0, x1)
    except (ZeroDivisionError, ValueError, OverflowError):
        return []
    if not all(math.isfinite(c) for c in contrib):
        return []
    total = identity.value(x1) - identity.value(x0)
    return [
        DecompositionTerm(
            metric=m, exponent=e, event=x1[i], reference=x0[i], contribution=contrib[i],
            share_of_change=(contrib[i] / total) if abs(total) > 1e-12 else None,
        )
        for i, (m, e) in enumerate(identity.factors)
    ]


def _specificity(event: dict[str, float], ref: dict[str, float]) -> float | None:
    """Share of the change NOT explained by every segment scaling proportionally.

    0 = all segments moved by the same percentage (broad-based); 1 = the change
    sits entirely in segments whose share of the total moved.
    """
    keys = set(event) | set(ref)
    ev_total = sum(event.get(k, 0.0) for k in keys)
    rf_total = sum(ref.get(k, 0.0) for k in keys)
    delta = ev_total - rf_total
    if abs(rf_total) < 1e-12:
        return None
    denom = sum(abs(event.get(k, 0.0) - ref.get(k, 0.0)) for k in keys)
    if denom < 1e-12:
        return 0.0
    dev = sum(abs((event.get(k, 0.0) - ref.get(k, 0.0)) - ref.get(k, 0.0) / rf_total * delta) for k in keys)
    return min(1.0, dev / denom)


def _segment_values(seg: dict[str, Series], days: list[date]) -> dict[str, float]:
    return {s: sum(float(v.get(d, 0.0)) for d in days if _finite(v.get(d, 0.0))) for s, v in seg.items()}


def _segment_ref(seg: dict[str, Series], window: EventWindow) -> dict[str, float]:
    out: dict[str, float] = {}
    for s, v in seg.items():
        total = 0.0
        for d in window.event_days:
            refs = window.reference[d]
            total += _mean([float(v.get(r, 0.0)) * window.factor(d, r) for r in refs]) if refs else 0.0
        out[s] = total
    return out


def _typical_specificity(seg: dict[str, Series], window: EventWindow) -> float | None:
    """Specificity a normal day shows: each reference day vs the other references."""
    vals: list[float] = []
    for d in window.event_days:
        refs = window.reference[d]
        for r in refs:
            others = [o for o in refs if o != r]
            if not others:
                continue
            ev = {s: float(v.get(r, 0.0)) for s, v in seg.items()}
            rf = {s: _mean([float(v.get(o, 0.0)) for o in others]) for s, v in seg.items()}
            sp = _specificity(ev, rf)
            if sp is not None:
                vals.append(sp)
    return _median(vals) if vals else None


def _is_identifier_like(seg: dict[str, Series]) -> bool:
    """A dimension whose values almost never repeat across days (ids, timestamps)."""
    if not seg:
        return True
    days_present = [sum(1 for v in s.values() if _finite(v) and v != 0) for s in seg.values()]
    return _median([float(x) for x in days_present]) < 2


def _analyse_additive_dimension(
    dim: str, seg: dict[str, Series], window: EventWindow, total_delta: float, total_reference: float,
    same_weekday: bool = True,
) -> DimensionFinding | None:
    if _is_identifier_like(seg):
        return None
    ev = _segment_values(seg, window.event_days)
    rf = _segment_ref(seg, window)
    keys = sorted(set(ev) | set(rf))
    seg_delta = sum(ev.get(k, 0.0) - rf.get(k, 0.0) for k in keys)
    rf_total = sum(abs(v) for v in rf.values()) or 1.0
    n_ev = len(window.event_days)
    moves: list[SegmentMove] = []
    for k in keys:
        d = ev.get(k, 0.0) - rf.get(k, 0.0)
        noise = _abs_noise(seg.get(k, {}), window.history_days, set(window.excluded_outliers), same_weekday)
        z = d / (noise * math.sqrt(n_ev)) if noise else None
        moves.append(SegmentMove(
            segment=k, event=ev.get(k, 0.0), reference=rf.get(k, 0.0), delta=d,
            share_of_change=(d / total_delta) if abs(total_delta) > 1e-12 else None,
            z_score=z, significant=(z is not None and abs(z) >= P.DIAG_EVENT_Z),
        ))
    moves.sort(key=lambda m: -abs(m.delta))
    spec = _specificity(ev, rf)
    typical = _typical_specificity(seg, window)
    # Localised: segments that moved beyond their own noise, in the event's
    # direction, carry most of the change while holding a minority of volume.
    hot = [m for m in moves if m.significant and _sign(m.delta) == _sign(total_delta)]
    hot_share = sum(m.delta for m in hot) / total_delta if abs(total_delta) > 1e-12 else 0.0
    hot_mass = sum(abs(rf.get(m.segment, 0.0)) for m in hot) / rf_total
    localised = bool(hot) and hot_share >= 0.5 and hot_mass < 0.5
    broad = not localised and spec is not None and spec <= P.DIAG_BROAD_BASED_MAX
    note = ""
    if localised:
        note = f"{len(hot)} segment(s) holding {hot_mass:.0%} of normal volume carry {hot_share:.0%} of the change"
    return DimensionFinding(
        dimension=dim, kind="additive", n_segments=len(keys),
        coverage=(seg_delta / total_delta) if abs(total_delta) > 1e-12 else None,
        specificity=spec, typical_specificity=typical, localised=localised, broad_based=broad,
        top=moves[: P.DIAG_MAX_SEGMENTS_REPORTED], note=note,
        volume_coverage=(sum(rf.values()) / total_reference) if abs(total_reference) > 1e-12 else None,
        hot_share=hot_share,
    )


def _analyse_rate_dimension(
    dim: str, rate: dict[str, Series], weight: dict[str, Series], window: EventWindow, total_rate: Series,
    total_weight: Series | None = None,
) -> DimensionFinding | None:
    """Mix/rate split of a weighted-average metric across one dimension."""
    if _is_identifier_like(weight):
        return None
    segs = sorted(set(rate) & set(weight))
    if not segs:
        return None
    # Verify the weighted-average identity on reference days before trusting it.
    check: list[float] = []
    for d in {r for refs in window.reference.values() for r in refs}:
        w = {s: float(weight[s].get(d, 0.0)) for s in segs}
        wt = sum(w.values())
        if wt <= 0 or d not in total_rate:
            continue
        implied = sum(w[s] * float(rate[s].get(d, 0.0)) for s in segs) / wt
        if abs(total_rate[d]) > 1e-12:
            check.append(abs(implied - total_rate[d]) / abs(total_rate[d]))
    if not check or _median(check) > 5 * P.DIAG_IDENTITY_TOLERANCE:
        return None

    def agg(days_by_event: dict[date, list[date]] | None) -> tuple[dict[str, float], dict[str, float]]:
        w_out: dict[str, float] = {}
        n_out: dict[str, float] = {}
        for s in segs:
            w_sum = 0.0
            n_sum = 0.0
            for d in window.event_days:
                days = [d] if days_by_event is None else days_by_event[d]
                if not days:
                    continue
                fac = [1.0 if days_by_event is None else window.factor(d, x) for x in days]
                # The outcome's level factor scales the rate, so it rides on the
                # numerator only; weights (the volume mix) stay as observed.
                ws = [float(weight[s].get(x, 0.0)) for x in days]
                ns = [float(weight[s].get(x, 0.0)) * float(rate[s].get(x, 0.0)) * f for x, f in zip(days, fac, strict=True)]
                w_sum += _mean(ws)
                n_sum += _mean(ns)
            w_out[s] = w_sum
            n_out[s] = n_sum
        return w_out, n_out

    w1, n1 = agg(None)
    w0, n0 = agg(window.reference)
    W1, W0 = sum(w1.values()), sum(w0.values())
    if W1 <= 0 or W0 <= 0:
        return None
    r1 = {s: n1[s] / w1[s] if w1[s] > 0 else 0.0 for s in segs}
    r0 = {s: n0[s] / w0[s] if w0[s] > 0 else 0.0 for s in segs}
    s1 = {s: w1[s] / W1 for s in segs}
    s0 = {s: w0[s] / W0 for s in segs}
    R1 = sum(s1[s] * r1[s] for s in segs)
    R0 = sum(s0[s] * r0[s] for s in segs)
    dR = R1 - R0
    proportion = all(0.0 <= r <= 1.0 for r in (*r0.values(), *r1.values()))
    moves: list[SegmentMove] = []
    mix_total = rate_total = 0.0
    for s in segs:
        mix = (s1[s] - s0[s]) * (r0[s] + r1[s]) / 2.0
        rate_eff = (r1[s] - r0[s]) * (s0[s] + s1[s]) / 2.0
        mix_total += mix
        rate_total += rate_eff
        z = None
        sig = None
        if proportion and w1[s] > 0 and w0[s] > 0:
            pooled = (n1[s] + n0[s]) / (w1[s] + w0[s])
            se = math.sqrt(max(pooled * (1 - pooled), 0.0) * (1 / w1[s] + 1 / w0[s]))
            z = (r1[s] - r0[s]) / se if se > 0 else None
            sig = z is not None and abs(z) >= P.DIAG_EVENT_Z
        moves.append(SegmentMove(
            segment=s, event=w1[s], reference=w0[s], delta=w1[s] - w0[s],
            share_of_change=((mix + rate_eff) / dR) if abs(dR) > 1e-12 else None,
            rate_event=r1[s], rate_reference=r0[s], rate_effect=rate_eff, mix_effect=mix,
            z_score=z, significant=sig,
        ))
    moves.sort(key=lambda m: -abs((m.rate_effect or 0.0) + (m.mix_effect or 0.0)))
    # Simpson: the aggregate moves one way while the within-segment rates move
    # the other (or not at all) and the shift in mix carries the change.
    simpson = _sign(dR) != 0 and _sign(rate_total) != _sign(dR) and abs(mix_total) > abs(rate_total)
    spec_ev = {s: n1[s] for s in segs}
    spec_rf = {s: n0[s] for s in segs}
    spec = _specificity(spec_ev, spec_rf)
    hot = [m for m in moves if m.significant and _sign((m.rate_effect or 0) + (m.mix_effect or 0)) == _sign(dR)]
    hot_share = sum((m.rate_effect or 0) + (m.mix_effect or 0) for m in hot) / dR if abs(dR) > 1e-12 else 0.0
    hot_mass = sum(s0[m.segment] for m in hot)
    rate_localised = bool(hot) and hot_share >= 0.5 and hot_mass < 0.5
    note = ""
    if rate_localised:
        note = (
            f"{len(hot)} segment(s) holding {hot_mass:.0%} of the volume carry {hot_share:.0%} of the change "
            "through their own rate"
        )
    if simpson:
        note = (
            "Simpson's paradox: the overall rate moved because the mix shifted toward "
            "segments with different rates, while the rates within segments moved the other way or held."
        )
    return DimensionFinding(
        dimension=dim, kind="rate", n_segments=len(segs), coverage=((mix_total + rate_total) / dR) if abs(dR) > 1e-12 else None,
        specificity=spec, typical_specificity=None, localised=simpson or rate_localised,
        broad_based=not (simpson or rate_localised) and spec is not None and spec <= P.DIAG_BROAD_BASED_MAX,
        top=moves[: P.DIAG_MAX_SEGMENTS_REPORTED], mix_effect=mix_total, rate_effect=rate_total,
        simpsons_paradox=simpson, note=note,
        volume_coverage=_rate_coverage(w0, total_weight, window),
        hot_share=hot_share,
    )


def _rate_coverage(w0: dict[str, float], total_weight: Series | None, window: EventWindow) -> float | None:
    if not total_weight:
        return None
    ref_total = 0.0
    for d in window.event_days:
        vals = [float(total_weight[r]) for r in window.reference[d] if _finite(total_weight.get(r))]
        if not vals:
            return None
        ref_total += _mean(vals)
    return sum(w0.values()) / ref_total if ref_total > 0 else None


def _residual_correlation(a: Series, b: Series, days: list[date]) -> float | None:
    fa, fb = fit_calendar(a, days), fit_calendar(b, days)
    if fa is None or fb is None:
        return None
    common = [d for d in days if d in fa.residual_z and d in fb.residual_z]
    if len(common) < P.MIN_OBSERVATION_ROWS:
        return None
    xa = [fa.residual_z[d] for d in common]
    xb = [fb.residual_z[d] for d in common]
    ma, mb = _mean(xa), _mean(xb)
    sa = math.sqrt(sum((x - ma) ** 2 for x in xa))
    sb = math.sqrt(sum((x - mb) ** 2 for x in xb))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(xa, xb, strict=True)) / (sa * sb)


def _classify_driver(
    est: TimeSeriesEffect, moved: bool, aligned: bool
) -> tuple[Classification, str]:
    lo, hi = (est.confidence_interval + [math.nan, math.nan])[:2]
    significant = _finite(lo) and _finite(hi) and (lo > 0 or hi < 0)
    refs = {r["name"]: bool(r.get("passed")) for r in est.refutations}
    placebo_ok = refs.get("time_shift_placebo", False) and refs.get("future_treatment_placebo", False)
    all_ok = bool(refs) and all(refs.values())
    direction = est.temporal.get("direction")
    naive_p = est.naive_association.get("p_value")
    if not significant:
        if naive_p is not None and naive_p < P.DIAG_ALPHA:
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


def _own_window(
    series: Series, history: list[date], event_days: list[date], *, additive: bool, kind: str,
    extra_outliers: frozenset[date] | set[date] = frozenset(),
) -> tuple[EventWindow | None, SeasonalProfile | None]:
    """An event window referenced on a series' OWN level-adjusted references.

    ``extra_outliers``: days abnormal for a linked metric (the outcome or a
    partner component describe the same events), excluded here too.
    """
    prof = seasonal_profile(series, history, len(event_days), additive=additive)
    if prof is None:
        return None, None
    if extra_outliers:
        prof.outliers = set(prof.outliers) | set(extra_outliers)
        prof.referencer = Referencer(series, history, prof.outliers, prof.same_weekday, prof.referencer.weeks)
    refs = {d: prof.referencer.refs(d) for d in event_days}
    if not all(len(v) >= 2 for v in refs.values()):
        return None, prof
    fac: dict[date, dict[date, float]] = {}
    start = min(event_days)
    for d in event_days:
        refs[d], fac[d] = prof.referencer.trimmed(start, refs[d])
    return EventWindow(event_days, history, refs, sorted(prof.outliers), kind, fac), prof


def _chain_step(
    inp: DiagnosisInput, window: EventWindow, ident: Identity, f_ev: float, f_rf: float,
) -> list[DecompositionTerm]:
    """Shapley split of an additive metric's change over ``k x rate x base``.

    The base is additive (summed); the rate is the *effective* rate implied by
    the identity (metric / (k x base)), so the split is exact for the metric's
    own change — the approximation lives only in reading that effective rate
    as the catalogue rate, which the report says.
    """
    (rate, _), (base, _) = ident.factors
    linked = _linked_outliers(inp, window, [ident.outcome, rate, base])
    own, _ = _own_window(
        inp.series.get(base, {}), window.history_days, window.event_days, additive=True,
        kind=window.reference_kind, extra_outliers=linked,
    )
    b_ev, b_rf = _additive_window(inp.series.get(base, {}), own or window, missing_as_zero=False)
    if b_ev is None or b_rf is None or b_ev <= 0 or b_rf <= 0:
        return []
    r_ev, r_rf = f_ev / (ident.k * b_ev), f_rf / (ident.k * b_rf)

    def f(x: list[float]) -> float:
        return ident.k * x[0] * x[1]

    parts = shapley(f, [r_rf, b_rf], [r_ev, b_ev])
    total = f_ev - f_rf
    # Show the catalogue rate as observed (its own reference); the split above
    # used the rate implied by the identity, which differs only by the
    # cross-system approximation.
    r_win, _ = _own_window(
        inp.series.get(rate, {}), window.history_days, window.event_days, additive=False,
        kind=window.reference_kind, extra_outliers=linked,
    )
    obs_ev, obs_rf = _daily_window(inp.series.get(rate, {}), r_win or window)
    if obs_ev is not None and obs_rf is not None:
        r_ev, r_rf = obs_ev, obs_rf
    return [
        DecompositionTerm(metric=m, exponent=1.0, event=e, reference=r, contribution=c,
                          share_of_change=(c / total) if abs(total) > 1e-12 else None, basis="approximate identity")
        for m, e, r, c in ((rate, r_ev, r_rf, parts[0]), (base, b_ev, b_rf, parts[1]))
    ]


def diagnose(inp: DiagnosisInput) -> DiagnosisReport:
    y = inp.outcome
    ys = inp.series.get(y, {})
    event_days = sorted(inp.event_days)
    window_label = [d.isoformat() for d in event_days]
    quality: list[str] = []
    if not event_days:
        return DiagnosisReport(outcome=y, event_window=[], verdict="insufficient_data", headline="No event window given.", event=None)
    if any(d in inp.partial_days for d in event_days):
        quality.append(
            "The event window includes a day that is still in progress; a partial day always looks "
            "like a fall against complete days, so this comparison is not meaningful yet."
        )
        return DiagnosisReport(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline="The period asked about is not complete yet, so its change cannot be diagnosed.",
            event=None, data_quality=quality,
        )
    missing_event = [d for d in event_days if not _finite(ys.get(d))]
    if missing_event:
        return DiagnosisReport(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline=f"No data for {y} on {', '.join(d.isoformat() for d in missing_event)}.",
            event=None, data_quality=[f"{y} has no value on the event day(s)"],
        )
    start = min(event_days)
    history = sorted(d for d in ys if d < start and d not in inp.partial_days and _finite(ys[d]))
    expected_span = (start - min(history)).days if history else 0
    if history and len(history) < expected_span * 0.8:
        quality.append(f"{y} is missing {expected_span - len(history)} of {expected_span} history days")
    meta_y = inp.lineage.get(y, MetricMeta(y))
    n_ev = len(event_days)
    weight_metric = inp.denominators.get(y)
    profile = seasonal_profile(ys, history, n_ev, additive=meta_y.additive)
    if profile is None:
        return DiagnosisReport(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline=(
                f"Only {len(history)} days of history for {y}; too few to learn what a normal day looks like "
                "and tell a real change from noise."
            ),
            event=None, data_quality=quality,
        )
    outliers = profile.outliers
    referencer = profile.referencer
    refs = {d: referencer.refs(d) for d in event_days}
    ref_kind = "same weekday, level-adjusted" if profile.same_weekday else "nearest normal days (too little history for a same-weekday reference)"
    if any(len(v) < 2 for v in refs.values()):
        refs, ref_kind = reference_days(event_days, history, outliers, P.DIAG_REFERENCE_WEEKS)
        factors = {}
    else:
        factors = {}
        for d in event_days:
            refs[d], factors[d] = referencer.trimmed(start, refs[d])
    window = EventWindow(event_days, history, refs, sorted(outliers), ref_kind, factors)
    if outliers:
        quality.append(
            f"{len(outliers)} abnormal history day(s) excluded from the reference: "
            + ", ".join(d.isoformat() for d in sorted(outliers)[:8])
            + (" …" if len(outliers) > 8 else "")
        )

    identities = discover_identities(y, inp.lineage, inp.series, history)
    identity = identities[0] if identities else None
    y_ev, y_rf, agg_note = _event_value(y, inp, window, identity)
    if y_ev is None or y_rf is None or abs(y_rf) < 1e-12:
        return DiagnosisReport(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline=f"Could not form a reference for {y} from comparable past days.", event=None, data_quality=quality,
        )
    delta = y_ev - y_rf
    rel_err = _err(y_ev, y_rf, profile.use_log, profile.offset * (n_ev if meta_y.additive else 1))
    if rel_err is None:
        return DiagnosisReport(
            outcome=y, event_window=window_label, verdict="insufficient_data",
            headline=f"{y} cannot be compared with its reference on a common scale.", event=None, data_quality=quality,
        )
    z = profile.z(rel_err)
    unusual = abs(z) >= P.DIAG_EVENT_Z and _sign(delta) == _sign(z)
    notable = not unusual and abs(z) >= P.DIAG_EVENT_Z_NOTABLE and _sign(delta) == _sign(z)
    direction: Direction | None = None if _sign(delta) == 0 else ("up" if delta > 0 else "down")
    prev_days = [d - timedelta(days=n_ev) for d in event_days]
    prev_vals = [ys.get(d) for d in prev_days]
    previous: dict[str, Any] = {"days": [d.isoformat() for d in prev_days]}
    if all(_finite(v) for v in prev_vals):
        pv = sum(float(v) for v in prev_vals) if meta_y.additive else _mean([float(v) for v in prev_vals])
        ev_simple = sum(float(ys[d]) for d in event_days) if meta_y.additive else _mean([float(ys[d]) for d in event_days])
        previous.update({"value": pv, "delta": ev_simple - pv, "delta_pct": _rel(ev_simple - pv, pv)})
    prev_direction: Direction | None = None
    if previous.get("delta") and abs(previous.get("delta_pct") or 0.0) >= 0.05:
        prev_direction = "up" if previous["delta"] > 0 else "down"
    if inp.claimed_direction is None:
        premise = "not_stated"
    elif direction is not None and direction != inp.claimed_direction and (
        abs(z) >= 0.5 or abs(_rel(delta, y_rf) or 0.0) >= 0.10
    ):
        # it moved the other way by a material amount — unless the user's baseline
        # is the day before, where the asked direction does hold
        premise = "vs_previous_only" if prev_direction == inp.claimed_direction else "contradicted"
    elif not (unusual or notable):
        premise = "not_unusual"
    else:
        premise = "confirmed" if inp.claimed_direction == direction else "contradicted"

    sampling_extra: dict[str, Any] = {}
    if weight_metric and not meta_y.additive and 0.0 <= y_ev <= 1.0 and 0.0 <= y_rf <= 1.0:
        w_ev = sum(float(inp.series.get(weight_metric, {}).get(d, 0.0)) for d in event_days)
        if w_ev > 0 and 0 < y_rf < 1:
            sampling_extra["binomial_z"] = (y_ev - y_rf) / math.sqrt(y_rf * (1 - y_rf) / w_ev)
            sampling_extra["events_counted"] = y_ev * w_ev
    event = EventSummary(
        metric=y, actual=y_ev, reference=y_rf, delta=delta, delta_pct=_rel(delta, y_rf),
        expected_by_model=None, z_score=z, unusual=unusual, direction=direction, premise=premise,
        strength="strong" if unusual else ("moderate" if notable else "none"),
        previous_period=previous, reference_kind=ref_kind,
        reference_days={d.isoformat(): [r.isoformat() for r in refs[d]] for d in event_days},
        level_factors={d.isoformat(): {r.isoformat(): round(f, 4) for r, f in window.factors.get(d, {}).items()} for d in event_days},
        excluded_outlier_days=[d.isoformat() for d in sorted(outliers)], aggregation_note=agg_note,
        sampling={
            "typical_deviation": profile.scale, "scale": "log" if profile.use_log else "relative", "history_windows": len(profile.errors),
            "percentile_vs_history": profile.percentile(rel_err),
            **sampling_extra,
        },
    )

    report = DiagnosisReport(
        outcome=y, event_window=window_label, verdict="root_cause_not_identified", headline="",
        event=event, identities=[i.describe() for i in identities], data_quality=quality,
    )
    report.assumptions.append(
        f"Reference = {ref_kind}: the {P.DIAG_REFERENCE_WEEKS} most recent normal same-weekday days, each scaled by how "
        "the trailing 7-normal-day level changed since then, highest and lowest dropped, the rest averaged; "
        "significance = how far the event sits from its reference compared with how far every past day sat from "
        "its own reference (median/MAD of those deviations)."
    )

    # ---- layer 2: decomposition ---------------------------------------------------
    if identity is not None:
        report.decomposition = _decompose(inp, window, identity, y_ev, y_rf)

    # ---- layer 2b: next link — dominant additive component as rate x base ----------
    chain_target, t_ev, t_rf = (y, y_ev, y_rf) if meta_y.additive else (None, None, None)
    additive_terms = [t for t in report.decomposition if inp.lineage.get(t.metric, MetricMeta(t.metric)).additive]
    if additive_terms:
        lead = max(additive_terms, key=lambda t: abs(t.contribution))
        chain_target, t_ev, t_rf = lead.metric, lead.event, lead.reference
    if chain_target is not None and t_ev is not None and t_rf is not None:
        chains = discover_rate_chains(chain_target, inp.lineage, inp.series, history)
        if chains:
            ident_c = chains[0]
            report.chain_identity = ident_c.describe() + f" (agrees within {ident_c.dispersion:.0%} on a typical day)"
            report.chain = _chain_step(inp, window, ident_c, t_ev, t_rf)
            rate_m, base_m = ident_c.factors[0][0], ident_c.factors[1][0]
            r_window, _ = _own_window(inp.series[rate_m], history, event_days, additive=False, kind=ref_kind)
            for dim, seg in (inp.segments.get(rate_m) or {}).items():
                if r_window is None or dim not in (inp.segments.get(base_m) or {}):
                    continue
                f = _analyse_rate_dimension(dim, seg, inp.segments[base_m][dim], r_window, inp.series[rate_m], inp.series.get(base_m))
                if f is not None:
                    report.chain_dimensions.append(f)
            report.chain_dimensions.sort(key=lambda f: (not f.simpsons_paradox, not f.localised, -abs(f.hot_share or 0.0)))

    # ---- layer 3: localisation ------------------------------------------------------
    dims: list[DimensionFinding] = []
    for dim, seg in (inp.segments.get(y) or {}).items():
        finding: DimensionFinding | None = None
        if meta_y.additive:
            finding = _analyse_additive_dimension(dim, seg, window, delta, y_rf, profile.same_weekday)
        elif weight_metric and dim in (inp.segments.get(weight_metric) or {}):
            finding = _analyse_rate_dimension(
                dim, seg, inp.segments[weight_metric][dim], window, ys, inp.series.get(weight_metric)
            )
        if finding is not None:
            dims.append(finding)
    # Dimensions whose segments do not add back up to the whole (attribution
    # fields populated for a subset of rows) describe a slice, not the change.
    full = [f for f in dims if f.volume_coverage is None or f.volume_coverage >= P.DIAG_MIN_DIMENSION_COVERAGE]
    partial = [f for f in dims if f not in full]
    for f in partial:
        f.note = (f.note + "; " if f.note else "") + (
            f"its segments cover only {f.volume_coverage:.0%} of {y}, so it describes a slice of the change"
        )
    full.sort(key=lambda f: (not f.simpsons_paradox, not f.localised, f.broad_based, -abs(f.hot_share or 0.0)))
    partial.sort(key=lambda f: (not f.localised, -abs(f.hot_share or 0.0)))
    dims = full + partial
    report.dimensions = dims

    # ---- layer 4: drivers --------------------------------------------------------------
    excluded: dict[str, str] = {}
    desc = descendants(y, inp.lineage)
    identity_factors = {m for m, _ in identity.factors} if identity else set()
    for c in inp.candidate_drivers:
        if c == y:
            excluded[c] = "is the outcome itself"
        elif c in desc:
            excluded[c] = "is computed from the outcome (post-outcome variable), so it cannot cause it"
        elif c in identity_factors:
            excluded[c] = "is an arithmetic component of the outcome (see decomposition), not an upstream driver"
        elif c not in inp.series or len([d for d in history if _finite(inp.series[c].get(d))]) < P.MIN_OBSERVATION_ROWS:
            excluded[c] = "has too little history in the window"
    y_units = meta_y.entity_tokens(inp.scope_tokens)
    for c in inp.candidate_drivers:
        if c in excluded:
            continue
        shared_units = y_units & inp.lineage.get(c, MetricMeta(c)).entity_tokens(inp.scope_tokens)
        if shared_units:
            excluded[c] = (
                f"is measured on the same units as {y} ({', '.join(sorted(shared_units))}), so it describes the "
                "same events rather than causing them — see the decomposition and segments instead"
            )
    drivers = [c for c in inp.candidate_drivers if c not in excluded]
    numerator: Series = {}
    if weight_metric and weight_metric in inp.series:
        wser = inp.series[weight_metric]
        numerator = {d: float(ys[d]) * float(wser[d]) for d in ys if _finite(ys.get(d)) and _finite(wser.get(d))}
    # Additive factors of EVERY verified identity, not only the one decomposed: on
    # 2026-09-24 gross sales were decomposed as spend x ROAS, so orders (of
    # orders x AOV) were never compared and funnel purchases — the same orders,
    # counted by the web funnel — was named "the cause" (live MS3-73dac57c40).
    components = list(dict.fromkeys([
        *(t.metric for t in report.decomposition),
        *(m for ident in identities for m, _ in ident.factors),
    ]))
    components = [m for m in components if m != y and inp.lineage.get(m, MetricMeta(m)).additive]
    for c in list(drivers):
        twin = next(
            (m for m in components if m in inp.series and (rc := _lockstep_correlation(inp.series[c], inp.series[m], history, outliers, profile.same_weekday)) is not None and abs(rc) >= P.DIAG_COMEASURE_R),
            None,
        )
        if twin is not None:
            excluded[c] = f"counts the same events as {twin}, a component of {y} (lockstep on normal days); not a cause"
            drivers.remove(c)
            continue
        r = _lockstep_correlation(inp.series[c], ys, history, outliers, profile.same_weekday)
        rn = _lockstep_correlation(inp.series[c], numerator, history, outliers, profile.same_weekday) if numerator else None
        if rn is not None and abs(rn) >= P.DIAG_COMEASURE_R:
            excluded[c] = (
                f"moves in lockstep with the events {y} counts (rate × {weight_metric}, r={rn:.2f}); it measures "
                "the result, not a cause"
            )
            drivers.remove(c)
            continue
        if r is not None and abs(r) >= P.DIAG_COMEASURE_R:
            excluded[c] = (
                f"moves in lockstep with {y} even on normal days (r={r:.2f}), i.e. it measures the same events; "
                "not an upstream cause"
            )
            drivers.remove(c)
    # Same quantity measured elsewhere (ratio to the outcome nearly constant).
    for c in list(drivers):
        ratios = [
            float(inp.series[c][d]) / float(ys[d])
            for d in history if _finite(inp.series[c].get(d)) and _finite(ys.get(d)) and float(ys[d]) != 0
        ]
        if len(ratios) >= P.MIN_OBSERVATION_ROWS:
            m = _mean(ratios)
            sd = math.sqrt(_mean([(r - m) ** 2 for r in ratios]))
            if abs(m) > 1e-12 and sd / abs(m) < P.DIAG_COMEASURE_CV:
                excluded[c] = "moves in fixed proportion to the outcome (same quantity measured another way), not a cause"
                drivers.remove(c)
    for c, why in excluded.items():
        report.drivers.append(DriverFinding(driver=c, classification=None, status="excluded", reason=why))

    # Inseparable (highly correlated after calendar adjustment) candidate groups.
    clusters: dict[str, list[str]] = {c: [] for c in drivers}
    for a, b in combinations(drivers, 2):
        r = _residual_correlation(inp.series[a], inp.series[b], history)
        if r is not None and abs(r) >= P.DIAG_TREATMENT_CLUSTER_R:
            clusters[a].append(b)
            clusters[b].append(a)

    implicated: list[DriverFinding] = []
    for c in drivers:
        xs = inp.series[c]
        others = {o: inp.series[o] for o in drivers if o != c and o not in clusters[c]}
        # How far did the driver itself move at the event, against its own reference?
        x_meta = inp.lineage.get(c, MetricMeta(c))
        x_prof = seasonal_profile(xs, history, n_ev, additive=x_meta.additive)
        x_window = window
        if x_prof is not None:
            x_refs = {d: x_prof.referencer.refs(d) for d in event_days}
            if all(len(v) >= 2 for v in x_refs.values()):
                x_fac: dict[date, dict[date, float]] = {}
                for d in event_days:
                    x_refs[d], x_fac[d] = x_prof.referencer.trimmed(start, x_refs[d])
                x_window = EventWindow(event_days, history, x_refs, sorted(x_prof.outliers), ref_kind, x_fac)
        per_day_dx: list[float] = []
        for d in event_days:
            xr = _ref_value(xs, x_window.reference[d], x_window.factors.get(d))
            xv = xs.get(d)
            if xr is None or not _finite(xv):
                per_day_dx = []
                break
            per_day_dx.append(float(xv) - xr)
        if not per_day_dx:
            report.drivers.append(DriverFinding(
                driver=c, classification="insufficient_evidence", status="insufficient_evidence",
                reason=f"{c} has no value on the event day(s), or too little history for a reference",
            ))
            continue
        dx = sum(per_day_dx) if meta_y.additive else _mean(per_day_dx)
        x_ev, x_rf = (_additive_window(xs, x_window, missing_as_zero=False) if x_meta.additive else _daily_window(xs, x_window))
        x_z = None
        if x_prof is not None and x_rf is not None and x_ev is not None:
            e = _err(x_ev, x_rf, x_prof.use_log, x_prof.offset * (n_ev if x_meta.additive else 1))
            x_z = x_prof.z(e) if e is not None else None
        # Did the driver move on the event day or the day before (cause precedes effect)?
        lead_moves: list[float] = []
        if x_prof is not None:
            for d in [start - timedelta(days=1), *event_days]:
                if not _finite(xs.get(d)):
                    continue
                rr = x_prof.referencer.refs(d)
                if len(rr) < 2:
                    continue
                rr, f = x_prof.referencer.trimmed(d, rr)
                e = _err(float(xs[d]), _mean([float(xs[r]) * f[r] for r in rr]), x_prof.use_log, x_prof.offset)
                if e is not None:
                    lead_moves.append(x_prof.day_z(e))
        # Moved = beyond one typical deviation on the event day or the day before,
        # in the same direction as the shift that would explain the event.
        moved = any(abs(v) >= 1.0 and _sign(v) == _sign(dx) for v in lead_moves) or (
            x_z is not None and abs(x_z) >= 1.0 and _sign(x_z) == _sign(dx)
        )
        if not moved:
            # A driver that did not move cannot explain this event, whatever its
            # effect in general — skip the (expensive) estimation and say so.
            report.drivers.append(DriverFinding(
                driver=c, classification=None, status="ruled_out", ruled_out_because="did_not_move",
                reason=(
                    f"{c} did not move unusually on or just before the event"
                    + (f" (z={x_z:.1f})" if x_z is not None else "")
                    + ", so it cannot explain it"
                ),
                driver_event=x_ev, driver_reference=x_rf, driver_delta=dx, driver_z=x_z,
                driver_moved_before_or_at_event=False,
            ))
            continue
        est = estimate_time_series_effect(
            outcome=ys, treatment=xs, others=others, days=history,
            treatment_name=c, outcome_name=y, alpha=P.DIAG_ALPHA,
            refuter_tolerance=P.DIAG_REFUTER_TOLERANCE,
            min_rows_per_covariate=P.DIAG_MIN_ROWS_PER_COVARIATE,
            min_rows=P.MIN_OBSERVATION_ROWS, placebo_shifts=P.DIAG_PLACEBO_SHIFTS,
        )
        finding = DriverFinding(
            driver=c, classification=None, status="insufficient_evidence", reason=est.reason,
            n_rows=est.n_rows, estimator=f"DoWhy {est.estimator} on an explicit DAG; {est.uncertainty_method} interval",
            naive_association=est.naive_association, inseparable_from=sorted(clusters[c]),
        )
        if est.status != "estimated" or est.effect is None:
            finding.classification = "insufficient_evidence"
            report.drivers.append(finding)
            continue
        beta = est.effect
        lo, hi = est.confidence_interval
        contribution = beta * dx
        c_lo, c_hi = sorted((lo * dx, hi * dx))
        aligned = _sign(contribution) == _sign(delta) and _sign(delta) != 0
        cls, why = _classify_driver(est, moved, aligned)
        # classification = what the history says about X -> Y in general;
        # status = whether X explains THIS event (it must have moved, the right way).
        status: Literal["implicated", "ruled_out"] = "implicated"
        lo_, hi_ = est.confidence_interval
        if not (lo_ > 0 or hi_ < 0):
            status = "ruled_out"  # no adjusted effect: the reason already says so
            finding.ruled_out_because = "no_effect"
        elif not moved:
            status = "ruled_out"
            finding.ruled_out_because = "did_not_move"
            why = f"{c} did not move unusually on or just before the event" + (f" (z={x_z:.1f})" if x_z is not None else "") + f"; {why}"
        elif not aligned:
            status = "ruled_out"
            finding.ruled_out_because = "wrong_direction"
            why = f"{c} moved in the direction that would push {y} the other way; {why}"
        finding.classification = cls
        finding.status = status
        finding.reason = why
        finding.effect_per_unit = beta
        finding.effect_ci = [lo, hi]
        finding.p_value = est.p_value
        finding.driver_event = x_ev
        finding.driver_reference = x_rf
        finding.driver_delta = dx
        finding.driver_z = x_z
        finding.driver_moved_before_or_at_event = moved
        finding.contribution = contribution
        finding.contribution_ci = [c_lo, c_hi]
        # The model is daily: additive outcomes sum the event days, rates average them.
        finding.share_of_change = contribution / delta if abs(delta) > 1e-12 else None
        finding.causal_path = f"{c} → {y}"
        finding.adjustment_set = est.adjustment_set
        finding.graph_edges = [list(e) for e in est.graph_edges]
        finding.identified_estimand = est.identified_estimand
        finding.direction_evidence = est.temporal
        finding.refutations = est.refutations
        finding.assumptions = [
            f"No unmeasured same-day common cause of {c} and {y} beyond day-of-week, trend and yesterday's values.",
            f"{y} does not cause {c} within the same day.",
            "Effect is linear in the range observed and stable over the history window.",
        ]
        report.drivers.append(finding)
        if status == "implicated" and cls in ("supported_cause", "likely_contributor"):
            implicated.append(finding)

    rank = {"supported_cause": 0, "likely_contributor": 1, "correlation": 2, "insufficient_evidence": 3, None: 4}
    status_rank = {"implicated": 0, "ruled_out": 1, "insufficient_evidence": 2, "excluded": 3}
    report.drivers.sort(key=lambda f: (status_rank[f.status], rank[f.classification], -abs(f.share_of_change or 0.0)))

    # ---- competing hypotheses & verdict ---------------------------------------------
    for f in report.drivers:
        if f.status != "implicated" or f.classification not in ("supported_cause", "likely_contributor"):
            report.competing_hypotheses.append({
                "hypothesis": f"{f.driver} drove the change", "status": f.status,
                "classification": f.classification, "reason": f.reason,
            })
    for dim in dims:
        if dim.broad_based:
            report.competing_hypotheses.append({
                "hypothesis": f"the change is specific to some {dim.dimension} values",
                "status": "ruled_out",
                "reason": f"every {dim.dimension} moved roughly in proportion (specificity {dim.specificity:.2f})",
            })
    if implicated:
        # One estimate per inseparable cluster: collinear drivers carry the same signal.
        counted: list[DriverFinding] = []
        for f in sorted(implicated, key=lambda f: -abs(f.contribution or 0.0)):
            if not any(f.driver in g.inseparable_from for g in counted):
                counted.append(f)
        explained = sum(f.contribution or 0.0 for f in counted)
        report.unexplained_share = 1.0 - explained / delta if abs(delta) > 1e-12 else None
        if len(implicated) > 1:
            report.assumptions.append(
                "Driver contributions are estimated one driver at a time; when drivers are linked they overlap "
                "and should not be summed."
            )
    if not (unusual or notable):
        report.verdict = "no_unusual_change"
    elif any(f.classification == "supported_cause" for f in implicated):
        report.verdict = "explained" if (report.unexplained_share is not None and abs(report.unexplained_share) < 0.5) else "partially_explained"
    elif implicated:
        report.verdict = "partially_explained"
    elif report.decomposition or report.chain or any(d.localised or d.simpsons_paradox for d in (*dims, *report.chain_dimensions)):
        report.verdict = "located_cause_not_identified"
    else:
        report.verdict = "root_cause_not_identified"
    cited = [y, *(t.metric for t in report.decomposition), *(t.metric for t in report.chain),
             *(f.driver for f in report.drivers if f.status in ("implicated", "ruled_out"))]
    for m in dict.fromkeys(cited):
        add = inp.lineage.get(m, MetricMeta(m)).additive
        for days, into in ((prev_days, report.previous_values), (event_days, report.event_values)):
            vals = [inp.series.get(m, {}).get(d) for d in days]
            if all(_finite(v) for v in vals):
                into[m] = sum(float(v) for v in vals) if add else _mean([float(v) for v in vals])
    report.headline = _headline(report)
    report.narrative = _narrative(report, inp.lineage)
    return report


def _fmt(v: float | None) -> str:
    if v is None or not math.isfinite(v):
        return "n/a"
    a = abs(v)
    if a >= 100:
        return f"{v:,.0f}"
    if a >= 1:
        return f"{v:,.2f}"
    return f"{v:.4g}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None or not math.isfinite(v) else f"{v * 100:+.1f}%"


def _covers_whole(d: DimensionFinding) -> bool:
    """Segments that add back up to the metric (attribution-only fields describe a slice)."""
    return d.volume_coverage is None or d.volume_coverage >= P.DIAG_MIN_DIMENSION_COVERAGE


def _opposite(direction: str | None) -> str:
    return "down" if direction == "up" else "up"


def _headline(r: DiagnosisReport) -> str:
    e = r.event
    if e is None:
        return r.headline
    move = f"{r.outcome} was {_fmt(e.actual)} vs a usual {_fmt(e.reference)} ({_pct(e.delta_pct)}, z={_fmt(e.z_score)})"
    if r.verdict == "no_unusual_change":
        claim = ""
        if e.premise == "contradicted":
            claim = f" Note it moved {e.direction}, the opposite of the direction asked about."
        elif e.premise == "vs_previous_only":
            claim = (
                f" It did go {_opposite(e.direction)} versus the day before ({_pct(e.previous_period.get('delta_pct'))}),"
                " but that was a move back toward its usual level, not an unusual change."
            )
        hot = next(
            (d for d in (*r.dimensions, *r.chain_dimensions) if _covers_whole(d) and d.localised and d.top and d.top[0].significant),
            None,
        )
        note = ""
        if hot is not None:
            s0 = hot.top[0]
            note = (
                f" One segment did move beyond its own normal range: {hot.dimension}={s0.segment} (z={_fmt(s0.z_score)})"
                " — worth watching, but it did not make the total unusual."
            )
        return f"{move}: within normal day-to-day variation, so there is no unusual change to explain.{claim}{note}"
    parts = [move + "."]
    if e.premise == "contradicted":
        parts.insert(0, f"Contrary to the question, {r.outcome} did not go {'down' if e.direction == 'up' else 'up'} — it went {e.direction}.")
    elif e.premise == "vs_previous_only":
        parts.insert(0, (
            f"{r.outcome} went {_opposite(e.direction)} versus the day before "
            f"({_pct(e.previous_period.get('delta_pct'))}) but {e.direction} versus its usual level for that weekday."
        ))
    if e.strength == "moderate":
        pct = e.sampling.get("percentile_vs_history", math.nan)
        tail = pct if e.direction == "down" else 1 - pct
        parts.append(
            f"That is a bigger swing than about {1 - tail:.0%} of normal days but not extreme, so treat it as a "
            "notable change rather than a clear-cut anomaly."
        )
    if r.decomposition:
        lead = max(r.decomposition, key=lambda t: abs(t.contribution))
        parts.append(f"Arithmetically, {lead.metric} accounts for {_pct(lead.share_of_change).lstrip('+')} of the change.")
    if r.chain:
        lead_c = max(r.chain, key=lambda t: abs(t.contribution))
        other = next(t for t in r.chain if t is not lead_c)
        parts.append(
            f"One level down, {r.chain_identity.split(' ')[0]} ≈ {lead_c.metric} × {other.metric}: {other.metric} moved "
            f"{_pct(_rel(other.event - other.reference, other.reference))}, {lead_c.metric} moved "
            f"{_pct(_rel(lead_c.event - lead_c.reference, lead_c.reference))}, so {lead_c.metric} carries most of it."
        )
        cdim = next((d for d in r.chain_dimensions if _covers_whole(d) and (d.simpsons_paradox or d.localised)), None)
        if cdim and cdim.top:
            parts.append(f"The {lead_c.metric} drop is concentrated in {cdim.dimension}={cdim.top[0].segment}." if (lead_c.contribution < 0) else
                         f"The {lead_c.metric} rise is concentrated in {cdim.dimension}={cdim.top[0].segment}.")
    top_dim = next((d for d in r.dimensions if _covers_whole(d) and (d.simpsons_paradox or d.localised)), None)
    if top_dim and top_dim.top:
        s = top_dim.top[0]
        parts.append(f"It is concentrated in {top_dim.dimension}={s.segment}.")
    elif r.dimensions and all(d.broad_based for d in r.dimensions):
        parts.append("It is broad-based across every segment checked.")
    causes = [f for f in r.drivers if f.status == "implicated" and f.classification in ("supported_cause", "likely_contributor")]
    if causes:
        parts.append("; ".join(
            f"{f.driver} is a {f.classification.replace('_', ' ')} (~{_pct(f.share_of_change).lstrip('+')} of the change)"
            for f in causes[:3]
        ) + ".")
    else:
        parts.append(
            "No upstream driver could be causally identified from the available data; the figures above say what "
            "and where it changed, not why."
            if (r.decomposition or r.chain or r.dimensions) else
            "No upstream driver could be causally identified from the available data."
        )
    return " ".join(parts)


# --------------------------------------------------------------------------- plain-language skeleton
def _label(metric: str, lineage: dict[str, MetricMeta]) -> str:
    meta = lineage.get(metric)
    return (meta.label if meta and meta.label and meta.label != metric else metric.replace("_", " ")).strip()


def _val(v: float | None, metric: str, lineage: dict[str, MetricMeta]) -> str:
    if v is None or not math.isfinite(v):
        return "n/a"
    unit = (lineage.get(metric).unit if lineage.get(metric) else "") or ""
    if unit.lower() in ("ratio", "percent", "%"):
        return f"{v * 100:.2f}%"
    if unit.isalpha() and unit.isupper() and len(unit) == 3:  # ISO currency code
        return f"{unit} {_fmt(v)}"
    return _fmt(v)


def _chg(new: float | None, old: float | None) -> str:
    if new is None or old is None or not math.isfinite(new) or not math.isfinite(old) or abs(old) < 1e-12:
        return "n/a"
    return f"{(new / old - 1) * 100:+.0f}%"


def _share(v: float | None) -> str:
    return "n/a" if v is None or not math.isfinite(v) else f"{abs(v) * 100:.0f}%"


def _narrative(r: DiagnosisReport, lineage: dict[str, MetricMeta]) -> list[str]:
    e = r.event
    if e is None:
        return [r.headline]
    name = _label(r.outcome, lineage)
    when = r.event_window[0] if len(r.event_window) == 1 else f"{r.event_window[0]} to {r.event_window[-1]}"
    out: list[str] = []
    happened = (
        f"WHAT HAPPENED: {name} was {_val(e.actual, r.outcome, lineage)} on {when}, against a usual "
        f"{_val(e.reference, r.outcome, lineage)} for that weekday ({_chg(e.actual, e.reference)}; 'usual' blends "
        "recent same weekdays and is not any single day's value)."
    )
    if e.previous_period.get("value") is not None:
        happened += f" Versus the day before it was {_chg(e.actual, e.previous_period['value'])}."
    if e.premise == "contradicted":
        happened += f" Note: it went {e.direction}, not {'down' if e.direction == 'up' else 'up'} as the question assumes."
    elif e.premise == "vs_previous_only":
        happened += (
            f" So it did go {_opposite(e.direction)} versus the day before, as the question says, but against its "
            f"usual level it went {e.direction}: LEAD with both comparisons — never say it did not go "
            f"{_opposite(e.direction)}."
        )
    happened += {
        "strong": " That is well outside its normal day-to-day range.",
        "moderate": " That is a larger swing than most normal days, but not extreme — a notable change, not a clear anomaly.",
        "none": " That is within its normal day-to-day range, so there is no unusual change to explain.",
    }[e.strength]
    if e.premise == "not_unusual" and abs(e.delta_pct or 0.0) < 0.10:
        happened += " Against its usual level it was essentially flat."
    out.append(happened)
    if r.decomposition and e.strength == "none":
        # Shares of a change that is within noise are meaningless (they explode as
        # the total nears zero); state the component moves only.
        out.append(
            "COMPONENTS (for context; the total did not move unusually): "
            + "; ".join(
                f"{_label(t.metric, lineage)} {_val(t.reference, t.metric, lineage)} → {_val(t.event, t.metric, lineage)} "
                f"({_chg(t.event, t.reference)})"
                for t in r.decomposition
            )
            + "."
        )
    elif r.decomposition:
        terms = sorted(r.decomposition, key=lambda t: -abs(t.contribution))
        out.append(
            "WHAT CHANGED (arithmetic, not a cause): "
            + "; ".join(
                f"{_label(t.metric, lineage)} went from {_val(t.reference, t.metric, lineage)} to "
                f"{_val(t.event, t.metric, lineage)} ({_chg(t.event, t.reference)}), "
                f"{'accounting for' if _sign(t.contribution) == _sign(e.delta or 0) else 'offsetting'} about "
                f"{_share(t.share_of_change)} of the change"
                for t in terms
            )
            + "."
        )
    if r.chain and e.strength != "none":
        target = r.chain_identity.split(" ")[0]
        lead_c = max(r.chain, key=lambda t: abs(t.contribution))
        out.append(
            f"ONE LEVEL DOWN: {_label(target, lineage)} ≈ "
            + " × ".join(_label(t.metric, lineage) for t in r.chain)
            + f" (approximately{'; ' + r.chain_identity.split('(')[-1].rstrip(')') if '(' in r.chain_identity else ''}): "
            + "; ".join(
                f"{_label(t.metric, lineage)} {_val(t.reference, t.metric, lineage)} → {_val(t.event, t.metric, lineage)} "
                f"({_chg(t.event, t.reference)})"
                for t in r.chain
            )
            + f", so {_label(lead_c.metric, lineage)} carries about {_share(lead_c.share_of_change)} of it."
        )
    where = next((d for d in r.chain_dimensions if _covers_whole(d) and (d.localised or d.simpsons_paradox)), None)
    where_metric = r.chain[0].metric if where is not None and r.chain else None
    if where is None:
        where = next((d for d in r.dimensions if _covers_whole(d) and (d.localised or d.simpsons_paradox)), None)
        where_metric = r.outcome if where is not None else None
    if where is not None and where.top and where_metric is not None:
        s0 = where.top[0]
        if where.kind == "rate":
            txt = (
                f"WHERE: by {where.dimension.replace('_', ' ')}, the {_label(where_metric, lineage).lower()} for "
                f"{s0.segment} went from {_val(s0.rate_reference, where_metric, lineage)} to "
                f"{_val(s0.rate_event, where_metric, lineage)}"
                + (" — beyond its own normal range" if s0.significant else "")
                + "."
            )
            if where.simpsons_paradox:
                txt += " The overall rate moved because the traffic mix shifted, while rates within each group moved the other way (Simpson's paradox)."
        else:
            txt = (
                f"WHERE: by {where.dimension.replace('_', ' ')}, {s0.segment} went from {_val(s0.reference, where_metric, lineage)} "
                f"to {_val(s0.event, where_metric, lineage)}, about {_share(s0.share_of_change)} of the change."
            )
        out.append(txt)
    elif (full := [d for d in r.dimensions if _covers_whole(d)][:3]) and all(d.broad_based for d in full):
        out.append(
            "WHERE: the change was broad-based — every "
            + ", ".join(d.dimension.replace("_", " ") for d in full)
            + " moved roughly in proportion, so no single segment explains it."
        )
    if len(r.previous_values) > 1:
        prev_label = r.event.previous_period.get("days", ["the day before"])
        out.append(
            f"PREVIOUS DAY ({', '.join(prev_label)}), if a comparison table needs it: "
            + "; ".join(f"{_label(m, lineage)} {_val(v, m, lineage)}" for m, v in r.previous_values.items())
            + "."
        )
        if r.event_values:
            out.append(
                f"EVENT DAY ({', '.join(r.event_window)}), the same metrics: "
                + "; ".join(f"{_label(m, lineage)} {_val(v, m, lineage)}" for m, v in r.event_values.items())
                + "."
            )
    causes = [f for f in r.drivers if f.status == "implicated" and f.classification in ("supported_cause", "likely_contributor")]
    assoc = [f for f in r.drivers if f.status == "implicated" and f.classification == "correlation"]
    if causes:
        for f in causes:
            kind = "a cause" if f.classification == "supported_cause" else "a likely contributor"
            lo, hi = sorted(f.contribution_ci) if len(f.contribution_ci) == 2 else (None, None)
            out.append(
                f"WHY: {_label(f.driver, lineage)} is {kind}. It moved {_chg(f.driver_event, f.driver_reference)} "
                f"({_val(f.driver_reference, f.driver, lineage)} → {_val(f.driver_event, f.driver, lineage)}); from the "
                f"past {f.n_rows} days that is worth about {_val(f.contribution, r.outcome, lineage)} of {name.lower()} "
                f"(95% range {_val(lo, r.outcome, lineage)} to {_val(hi, r.outcome, lineage)}), roughly "
                f"{_share(f.share_of_change)} of the change. Basis: {f.reason}."
            )
    else:
        out.append(
            "WHY: no upstream cause could be identified from the data — the figures above show what and where it "
            "changed, not why."
        )
    if assoc:
        out.append(
            "MOVED TOGETHER (not shown to be causes): "
            + ", ".join(_label(f.driver, lineage) for f in assoc)
            + " — the data cannot tell whether they drove the change or reacted to it."
        )
    groups = {  # (one, many)
        "did_not_move": ("did not change unusually, so it does not explain it", "did not change unusually, so they do not explain it"),
        "wrong_direction": ("moved the other way, so it would have pushed it the opposite direction",
                            "moved the other way, so they would have pushed it the opposite direction"),
        "no_effect": ("shows no effect on it once weekday, trend and the previous day are accounted for",
                      "show no effect on it once weekday, trend and the previous day are accounted for"),
    }
    parts = []
    for code, (one, many) in groups.items():
        names = [_label(f.driver, lineage) for f in r.drivers if f.status == "ruled_out" and f.ruled_out_because == code]
        if names:
            parts.append(", ".join(names) + " " + (one if len(names) == 1 else many))
    if parts:
        out.append("RULED OUT: " + "; ".join(parts) + ".")
    refuted = any(not rr.get("passed") for f in causes for rr in f.refutations)
    out.append(
        "CONFIDENCE: "
        + {"strong": "the change itself is clearly real", "moderate": "the change is notable but within the range of rare normal days", "none": "the change is within normal variation"}[e.strength]
        + "; "
        + (
            "cause estimates control for day-of-week, trend and the previous day, and passed placebo checks"
            + (" (one robustness check was weaker)" if refuted else "")
            if causes else "no causal estimate met the bar for a cause"
        )
        + ". Key assumption: no unmeasured same-day factor moved both."
    )
    return out

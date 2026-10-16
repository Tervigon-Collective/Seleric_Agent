"""What a normal day looks like for a series and how far an event sits from it: the seasonal profile (same-weekday or
recent-day reference, log or relative scale), the period-versus-period shift test and the lockstep correlation used to
spot a measure of the same events (moved from ``causal/diagnosis.py``, plan P2.2). Evidence basis: statistical."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from seleric_swarm.diagnosis.detect.detector import robust_center_scale
from seleric_swarm.diagnosis.detect.reference import Referencer
from seleric_swarm.diagnosis.detect.reference import prior_days as _prior_days
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _mean, _median


def _robust_center_scale(xs: list[float]) -> tuple[float, float]:
    return robust_center_scale(xs)


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
    series: Series, history: list[date], n_window: int, *, additive: bool, weeks: int = 0, cfg: DiagnosisConfig,
) -> SeasonalProfile | None:
    """Normal-day behaviour of a series, referenced the way its history supports.

    A same-weekday reference suits a series with a weekly pattern; one without
    (a rate whose level shifts) is predicted better by its nearest normal days,
    and forcing same-weekday on it widens "normal" until a real drop hides
    (live 2026-10-04: a halved click-through rate scored z=-1.3 against
    same-weekday references and z=-5 against the nearest days). The history
    decides: same-weekday only when it shows a weekday pattern.
    """
    pool = {d for d in history if _finite(series.get(d))}
    if len(pool) < cfg.min_observation_rows:
        return None
    long_enough = (max(pool) - min(pool)).days + 1 >= 21
    same_weekday = long_enough and _has_weekly_pattern(series, pool, cfg=cfg)
    return _seasonal_profile(series, history, n_window, additive=additive, weeks=weeks, same_weekday=same_weekday, cfg=cfg)


def _has_weekly_pattern(series: Series, pool: set[date], *, cfg: DiagnosisConfig) -> bool:
    """Whether the weekday explains a series' day-to-day deviations from its local level.

    Each day is compared with the median of the centred 7-day window around it
    (one of each weekday, so the local level carries no weekday effect), on the
    log scale for non-negative series; a Kruskal-Wallis test across weekdays
    then asks whether those deviations differ by weekday. Ranks keep a few
    abnormal days (an outage, a sale) from creating or hiding a pattern.
    """
    from scipy import stats

    use_log, offset = _transform(series, pool)
    by_weekday: dict[int, list[float]] = {}
    for d in pool:
        around = [float(series[d + timedelta(days=j)]) for j in range(-3, 4) if d + timedelta(days=j) in pool]
        if len(around) < 7:
            continue
        e = _err(float(series[d]), _median(around), use_log, offset)
        if e is not None:
            by_weekday.setdefault(d.weekday(), []).append(e)
    groups = [g for g in by_weekday.values() if len(g) >= 2]
    if len(groups) < 7:
        return True  # too little to tell: keep the conservative weekly reference
    try:
        _, p = stats.kruskal(*groups)
    except ValueError:  # every value identical
        return False
    return bool(math.isfinite(p) and p < cfg.alpha)


_PERIOD_KIND = "the comparison period"


def _period_shift_z(series: Series, event_days: list[date], baseline: list[date]) -> tuple[float, float | None]:
    """Signed z (and p) of a rank test between the two periods' daily values."""
    from scipy import stats

    ev = [float(series[d]) for d in event_days if _finite(series.get(d))]
    rf = [float(series[d]) for d in baseline if _finite(series.get(d))]
    if len(ev) < 3 or len(rf) < 3:
        return 0.0, None
    try:
        p = float(stats.mannwhitneyu(ev, rf, alternative="two-sided").pvalue)
    except ValueError:
        return 0.0, None
    if not math.isfinite(p):
        return 0.0, None
    z = float(stats.norm.isf(max(p, 1e-12) / 2.0))
    return (z if _median(ev) >= _median(rf) else -z), p


def _reference_kind(profile: SeasonalProfile) -> str:
    return "same weekday, level-adjusted" if profile.same_weekday else "nearest normal days, level-adjusted"


def _seasonal_profile(
    series: Series, history: list[date], n_window: int, *, additive: bool, weeks: int, same_weekday: bool, cfg: DiagnosisConfig,
) -> SeasonalProfile | None:
    weeks = weeks or cfg.reference_weeks
    pool = {d for d in history if _finite(series.get(d))}
    if len(pool) < cfg.min_observation_rows:
        return None
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
    if len(seed) >= cfg.min_observation_rows:
        c0, s0 = _robust_center_scale(list(seed.values()))
        outliers = {h for h, e in seed.items() if abs(e - c0) / s0 >= cfg.outlier_z}
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
        if len(day_errors) < cfg.min_observation_rows:
            return None
        # Scale from the days currently believed normal, so a long abnormal
        # stretch cannot widen "normal" until it hides itself.
        normal = [e for h, e in day_errors.items() if h not in outliers] or list(day_errors.values())
        c, sc = _robust_center_scale(normal)
        new = {h for h, e in day_errors.items() if abs(e - c) / sc >= cfg.outlier_z}
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
    if len(errors) < cfg.min_observation_rows:
        return None
    prof.errors = errors
    prof.center, prof.scale = _robust_center_scale(errors)
    return prof


def _abs_noise(series: Series, history: list[date], exclude: set[date], same_weekday: bool, *, cfg: DiagnosisConfig) -> float | None:
    """Robust absolute day noise of a (segment) series around its own reference."""
    pool = set(history)
    errs: list[float] = []
    for h in history:
        if h in exclude:
            continue
        refs = _prior_days(h, pool, cfg.reference_weeks, exclude, same_weekday)
        if len(refs) < 2:
            continue
        errs.append(float(series.get(h, 0.0)) - _mean([float(series.get(r, 0.0)) for r in refs]))
    if len(errs) < cfg.min_observation_rows:
        return None
    _, sc = _robust_center_scale(errs)
    return sc if sc > 1e-9 else None


def _lockstep_correlation(a: Series, b: Series, history: list[date], exclude: set[date], same_weekday: bool, *, cfg: DiagnosisConfig) -> float | None:
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
        ra = _prior_days(h, pa, cfg.reference_weeks, exclude, same_weekday)
        rb = _prior_days(h, pb, cfg.reference_weeks, exclude, same_weekday)
        if len(ra) < 2 or len(rb) < 2:
            continue
        ma, mb = _mean([float(a[r]) for r in ra]), _mean([float(b[r]) for r in rb])
        if abs(ma) < 1e-12 or abs(mb) < 1e-12:
            continue
        xa.append((float(a[h]) - ma) / abs(ma))
        xb.append((float(b[h]) - mb) / abs(mb))
    if len(xa) < cfg.min_observation_rows:
        return None
    m1, m2 = _mean(xa), _mean(xb)
    s1 = math.sqrt(sum((x - m1) ** 2 for x in xa))
    s2 = math.sqrt(sum((x - m2) ** 2 for x in xb))
    if s1 == 0 or s2 == 0:
        return None
    return sum((x - m1) * (y - m2) for x, y in zip(xa, xb, strict=True)) / (s1 * s2)

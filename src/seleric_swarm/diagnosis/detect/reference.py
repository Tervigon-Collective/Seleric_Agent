"""Level-adjusted same-weekday references (plan P1.4): the reference a day is compared with, moved here unchanged from
``causal/diagnosis.py`` (which imports it back under its old names). Pure; depends only on the shared detector."""

from __future__ import annotations

from datetime import date, timedelta

from seleric_swarm.diagnosis.detect.detector import is_finite, mean

Series = dict[date, float]


def prior_days(d: date, pool: set[date], k: int, exclude: set[date], same_weekday: bool) -> list[date]:
    if same_weekday:
        cands = [d - timedelta(days=7 * j) for j in range(1, 3 * k + 1)]
    else:
        cands = [d - timedelta(days=j) for j in range(1, 3 * k + 8)]
    return [c for c in cands if c in pool and c not in exclude][: (k if same_weekday else max(k, 7))]


LEVEL_DAYS = 7
LEVEL_FACTOR_BOUNDS = (0.2, 5.0)


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
        self.pool = {d for d in history if is_finite(series.get(d))}
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
            for x in (d - timedelta(days=j) for j in range(1, LEVEL_DAYS + 1))
            if x in self.pool and x not in self.outliers
        ]
        lvl = mean(vals) if len(vals) >= 4 else None
        self._levels[d] = lvl
        return lvl

    def refs(self, d: date) -> list[date]:
        return prior_days(d, self.pool, self.weeks, self.outliers, self.same_weekday)

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
            if not self.same_weekday and (anchor - r).days <= LEVEL_DAYS:
                # A reference inside the anchor's own level window already IS the
                # current level; scaling it again extrapolates the recent trend.
                out[r] = 1.0
                continue
            then = self.level_before(r)
            if now is None or then is None or then <= 0 or now < 0:
                return {r: 1.0 for r in refs}
            lo, hi = LEVEL_FACTOR_BOUNDS
            out[r] = min(hi, max(lo, now / then))
        return out

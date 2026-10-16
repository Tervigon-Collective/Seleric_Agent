"""Identity discovery for WHAT: candidate identities and rate x base chains come from catalogue lineage and are accepted
only when the data reproduce them day by day (moved from ``causal/diagnosis.py``, plan P2.2). Evidence basis: identity."""

from __future__ import annotations

import math
from datetime import date
from itertools import combinations, permutations

from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.identity import Identity
from seleric_swarm.diagnosis.domain.lineage import MetricMeta, descendants
from seleric_swarm.diagnosis.domain.numeric import Series, _finite, _median


def _verify(
    outcome: str, factors: list[tuple[str, float]], series: dict[str, Series], days: list[date],
    *, tolerance: float | None = None, cfg: DiagnosisConfig,
) -> Identity | None:
    tol = cfg.identity_tolerance if tolerance is None else tolerance
    ks: list[float] = []
    for d in days:
        y = series.get(outcome, {}).get(d)
        xs = [series.get(m, {}).get(d) for m, _ in factors]
        if not _finite(y):
            continue
        finite_xs = [float(x) for x in xs if _finite(x)]
        if len(finite_xs) != len(xs):
            continue
        prod = 1.0
        ok = True
        for (_, e), x in zip(factors, finite_xs, strict=True):
            if x <= 0:
                ok = False
                break
            prod *= x**e
        if not ok or y == 0 or prod == 0:
            continue
        ks.append(float(y) / prod)
    if len(ks) < cfg.min_observation_rows:
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
        approximate=dispersion > cfg.identity_tolerance,
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
    metric: str, lineage: dict[str, MetricMeta], series: dict[str, Series], days: list[date], *, cfg: DiagnosisConfig
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
        ident = _verify(metric, [(rate, 1.0), (base, 1.0)], series, days, tolerance=cfg.approx_identity_tolerance, cfg=cfg)
        if ident is not None and 0.5 <= ident.k <= 2.0:
            ident.approximate = True
            found.append(ident)
    return sorted(found, key=lambda i: i.dispersion)


def discover_identities(
    outcome: str, lineage: dict[str, MetricMeta], series: dict[str, Series], days: list[date], *, cfg: DiagnosisConfig
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
        ident = _verify(outcome, factors, series, days, cfg=cfg)
        if ident is not None:
            found[key] = ident
    # Forward identities (built only from Y's own components) first.
    return sorted(found.values(), key=lambda i: (any(m in desc for m, _ in i.factors), i.dispersion, len(i.factors)))

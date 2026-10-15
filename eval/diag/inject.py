"""Injection library (plan P0.3): plant a known cause into a real world fixture, with ground truth.

Pure and seeded: the same fixture, injections, graph and seed give the same world. Nothing here imports the
engine, and no metric or dimension name is written in this file: every name comes from the case.

Mechanics. An injection changes one metric (optionally in one segment) from its onset. ``effects`` carry the change
down the reviewed graph, hop by hop: a hop ``from -> to`` must be a mechanism edge of the vendored graph and its lag
must lie inside the edge's lag range, otherwise the case is refused. The relative change of ``to`` is
``elasticity * relative change of from``, applied to the same segment when ``to`` has it, otherwise to the total in
proportion to that segment's share of ``from`` before the onset. Ratio metrics are then recomputed from their
components (``ratio_components`` in the catalogue definition), so accounting identities hold in the planted world.
Segments of any other dimension of a changed metric are dropped (they would no longer add up) and listed in
``dropped``. Optional multiplicative noise (``noise_cv``) is drawn from a stream derived from the seed.

Planted truth is returned as ``Planted`` (path, onset, size) for the scorer.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import case_format as C
import numpy as np
import world_fixture as W

SHARE_WINDOW_DAYS = 28  # pre-onset window that fixes a segment's share of its metric
MECHANISM = "mechanism"


class InjectionError(ValueError):
    pass


@dataclass(frozen=True)
class Planted:
    type: str
    metric: str
    onset: date
    scope: dict[str, str]
    factor: float
    path: tuple[tuple[str, str], ...]  # graph hops the effect travelled
    real: bool  # False: a measurement break, nothing real changed


@dataclass
class InjectedWorld:
    series: dict[str, dict[date, float]]
    segments: dict[str, dict[str, dict[str, dict[date, float]]]]
    planted: list[Planted] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (metric, dimension) segment sets made stale
    recomputed: list[str] = field(default_factory=list)  # ratio metrics rebuilt from components


def _stream(seed: int, name: str) -> np.random.Generator:
    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def _edges(graph: dict[str, Any]) -> dict[tuple[str, str], list[int]]:
    return {(e["from"], e["to"]): list(e.get("lag_days") or [0, 0]) for e in graph["edges"] if e["kind"] == MECHANISM}


def _scope(scope: dict[str, str]) -> tuple[str, str] | None:
    if len(scope) > 1:
        raise InjectionError(f"scope has {len(scope)} dimensions; one is supported")
    return next(iter(scope.items())) if scope else None


def _days_from(series: dict[date, float], start: date, until: date | None) -> list[date]:
    return [d for d in sorted(series) if d >= start and (until is None or d <= until)]


def _share(world: InjectedWorld, metric: str, dim: str, seg: str, onset: date) -> float:
    lo = onset - timedelta(days=SHARE_WINDOW_DAYS)
    total = sum(v for d, v in world.series.get(metric, {}).items() if lo <= d < onset)
    part = sum(v for d, v in world.segments.get(metric, {}).get(dim, {}).get(seg, {}).items() if lo <= d < onset)
    if total <= 0:
        raise InjectionError(f"{metric} has no volume before {onset}; cannot fix the share of {dim}={seg}")
    return part / total


def _factor_at(day: date, onset: date, lag: int, factor: float, ramp_days: int) -> float:
    start = onset + timedelta(days=lag)
    if day < start:
        return 1.0
    if ramp_days <= 0:
        return factor
    return 1.0 + (factor - 1.0) * min(1.0, (day - start).days / ramp_days)


def _scale(world: InjectedWorld, metric: str, scope: tuple[str, str] | None, factors: dict[date, float]) -> None:
    """Multiply ``metric`` (in ``scope``) by per-day factors; keep totals equal to the sum of the changed segment."""
    totals = world.series.get(metric)
    if totals is None:
        raise InjectionError(f"metric {metric!r} is not in the fixture")
    if scope is None:
        for d, f in factors.items():
            if d in totals:
                totals[d] *= f
        for dim in list(world.segments.get(metric, {})):
            world.dropped.append((metric, dim))
            del world.segments[metric][dim]
        return
    dim, seg = scope
    segs = world.segments.get(metric, {}).get(dim, {}).get(seg)
    if segs is None:
        raise InjectionError(f"{metric} has no segment {dim}={seg!r} in the fixture")
    for d, f in factors.items():
        if d in segs:
            if d in totals:
                totals[d] += segs[d] * (f - 1.0)
            segs[d] *= f
    for other in [x for x in world.segments[metric] if x != dim]:
        world.dropped.append((metric, other))
        del world.segments[metric][other]


def _recompute_ratios(world: InjectedWorld, definitions: dict[str, Any], changed: set[str], from_day: date) -> None:
    for mid, d in sorted(definitions.items()):
        rc = d.get("ratio_components") or {}
        num, den = rc.get("numerator"), rc.get("denominator")
        if not num or not den or not ({num, den} & changed) or num not in world.series or den not in world.series:
            continue
        mult = float(rc.get("multiplier") or 1.0)
        for day, dv in world.series[den].items():
            if day >= from_day and dv and day in world.series[num] and mid in world.series:
                world.series[mid][day] = mult * world.series[num][day] / dv
        for dim, segs_n in world.segments.get(num, {}).items():
            segs_d = world.segments.get(den, {}).get(dim)
            if segs_d is None:
                continue
            target = world.segments.setdefault(mid, {}).setdefault(dim, {})
            for seg, sn in segs_n.items():
                sd = segs_d.get(seg, {})
                row = target.setdefault(seg, {})
                for day, dv in sd.items():
                    if day >= from_day and dv and day in sn:
                        row[day] = mult * sn[day] / dv
        if mid not in world.recomputed:
            world.recomputed.append(mid)
        changed.add(mid)


def _noise(world: InjectedWorld, metric: str, cv: float, rng: np.random.Generator, days: list[date]) -> None:
    if cv <= 0:
        return
    for d in days:
        if d in world.series.get(metric, {}):
            world.series[metric][d] *= float(np.exp(rng.normal(0.0, cv)))


def apply_injections(fx: W.Fixture, injections: tuple[C.Injection, ...], graph: dict[str, Any], seed: int = 0) -> InjectedWorld:
    world = InjectedWorld(copy.deepcopy(fx.series), copy.deepcopy(fx.segments))
    mech = _edges(graph)
    changed: set[str] = set()
    first_day: date | None = None
    for i, inj in enumerate(injections):
        p = inj.params
        if inj.onset is None:
            raise InjectionError(f"injection {i} ({inj.type}) needs an onset")
        if not fx.start < inj.onset <= fx.end:
            raise InjectionError(f"injection {i}: onset {inj.onset} outside the fixture window")
        first_day = min(first_day or inj.onset, inj.onset)
        scope_pair = _scope(dict(p.get("scope") or {}))
        until = date.fromisoformat(p["until"]) if p.get("until") else None
        factor = float(p["factor"])
        ramp = int(p.get("ramp_days") or 0)
        rng = _stream(seed, f"{i}:{inj.type}")
        if inj.type not in ("scale", "measurement_break"):
            raise InjectionError(f"unknown injection type {inj.type!r}")
        metric = str(p["metric"])
        days = _days_from(world.series[metric], inj.onset, until)
        _scale(world, metric, scope_pair, {d: _factor_at(d, inj.onset, 0, factor, ramp) for d in days})
        changed.add(metric)
        hops: list[tuple[str, str]] = []
        if inj.type == "measurement_break" and p.get("effects"):
            raise InjectionError("a measurement break changes only what is measured: it takes no effects")
        # Relative change of each metric on the chain, by day, so the next hop reads it.
        rel = {metric: {d: _factor_at(d, inj.onset, 0, factor, ramp) - 1.0 for d in days}}
        pristine = fx_world(world, fx)
        for eff in p.get("effects") or []:
            src, dst, lag = str(eff["from"]), str(eff["to"]), int(eff.get("lag_days", 0))
            if (src, dst) not in mech:
                raise InjectionError(f"{src} -> {dst} is not a mechanism edge of the graph")
            lo, hi = mech[(src, dst)]
            if not lo <= lag <= hi:
                raise InjectionError(f"{src} -> {dst}: lag {lag} outside the edge's range [{lo}, {hi}]")
            if src not in rel:
                raise InjectionError(f"{src} -> {dst}: {src} was not changed by this injection or an earlier hop")
            elasticity = float(eff["elasticity"])
            shifted = {d + timedelta(days=lag): r for d, r in rel[src].items()}
            has_seg = scope_pair is not None and scope_pair[1] in world.segments.get(dst, {}).get(scope_pair[0], {})
            scale_by = 1.0
            if scope_pair is not None and not has_seg:
                # The share of the changed segment in the source metric, falling back to the planted metric.
                holder = src if scope_pair[1] in pristine.segments.get(src, {}).get(scope_pair[0], {}) else metric
                scale_by = _share(pristine, holder, *scope_pair, inj.onset)
            factors = {d: 1.0 + elasticity * r * scale_by for d, r in shifted.items() if d in world.series[dst]}
            _scale(world, dst, scope_pair if has_seg else None, factors)
            changed.add(dst)
            rel[dst] = {d: f - 1.0 for d, f in factors.items()}
            hops.append((src, dst))
            if p.get("noise_cv"):
                _noise(world, dst, float(p["noise_cv"]), _stream(seed, f"{i}:{dst}"), sorted(factors))
        if p.get("noise_cv"):
            _noise(world, metric, float(p["noise_cv"]), rng, days)
        world.planted.append(Planted(inj.type, metric, inj.onset, dict(p.get("scope") or {}), factor, tuple(hops), inj.type != "measurement_break"))
    if first_day is not None:
        _recompute_ratios(world, fx.definitions, changed, first_day)
    return world


def fx_world(world: InjectedWorld, fx: W.Fixture) -> InjectedWorld:
    """The world as it was before any change, for share calculations (segments are read from the pristine fixture)."""
    return InjectedWorld(fx.series, fx.segments)

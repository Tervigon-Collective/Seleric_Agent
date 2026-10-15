"""Injection library (plan P0.3): plant a known cause into a real world fixture, with ground truth.

Pure and deterministic: the same fixture, injections and graph give the same world. Nothing here imports the
engine, and no metric or dimension name is written in this file: every name comes from the case.

Mechanics. An injection changes one metric (optionally in one segment) from its onset. ``effects`` carry the change
down the reviewed graph, hop by hop: a hop ``from -> to`` must be a mechanism edge of the vendored graph and its lag
must lie inside the edge's lag range, otherwise the case is refused. The relative change of ``to`` is
``elasticity * relative change of from``, applied to the same segment when ``to`` has it, otherwise to the total in
proportion to that segment's share of ``from`` before the onset. Ratio metrics are then recomputed from their
components (``ratio_components`` in the catalogue definition), so accounting identities hold in the planted world.
Segments of any other dimension of a changed metric are dropped (they would no longer add up) and listed in
``dropped``.

A real change moves everything the graph says is the same quantity: ``co_measure`` and ``twin`` partners present in
the fixture get the same change, a ``subset`` follows its superset proportionally and a superset follows its subset
by the subset's delta. A ``measurement_break`` moves only the measured metric: that divergence from its partners is
what distinguishes it from a real change. Additive compositions (``formula.composition``: signed components) are kept
exact by adding the components' deltas to the parent; an injection that changes a parent and its component at once
is refused. No noise is added: noise confined to the post-onset days would itself be a planted signal.

Planted truth is returned as ``Planted`` (path, onset, size) for the scorer.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import case_format as C
import world_fixture as W

SHARE_WINDOW_DAYS = 28  # pre-onset window that fixes a segment's share of its metric
MECHANISM = "mechanism"
COMPOSITION_PASSES = 4  # a composition is read from components that are themselves compositions; 4 levels is ample


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


def _scale(world: InjectedWorld, metric: str, scope: tuple[str, str] | None, factors: dict[date, float], *, spread: bool = False) -> None:
    """Multiply ``metric`` (in ``scope``) by per-day factors.

    With a scope the total moves by the changed segment's delta and other dimensions are dropped. Without a scope the
    total moves; ``spread`` says every segment moves by the same factor (the change is everywhere), otherwise the
    segment sets would no longer add up and are dropped.
    """
    totals = world.series.get(metric)
    if totals is None:
        raise InjectionError(f"metric {metric!r} is not in the fixture")
    if scope is None:
        for d, f in factors.items():
            if d in totals:
                totals[d] *= f
        for dim, segs in list(world.segments.get(metric, {}).items()):
            if spread:
                for row in segs.values():
                    for d, f in factors.items():
                        if d in row:
                            row[d] *= f
            else:
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


def _is_ratio(definitions: dict[str, Any], metric: str) -> bool:
    rc = (definitions.get(metric) or {}).get("ratio_components") or {}
    return bool(rc.get("numerator") and rc.get("denominator"))


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
        rebuilt: set[str] = set()
        for dim, segs_n in world.segments.get(num, {}).items():
            segs_d = world.segments.get(den, {}).get(dim)
            if segs_d is None:
                continue
            target = world.segments.setdefault(mid, {}).setdefault(dim, {})
            rebuilt.add(dim)
            for seg, sn in segs_n.items():
                row = target.setdefault(seg, {})
                for day, dv in segs_d.get(seg, {}).items():
                    if day >= from_day and dv and day in sn:
                        row[day] = mult * sn[day] / dv
        for dim in [x for x in world.segments.get(mid, {}) if x not in rebuilt]:
            world.dropped.append((mid, dim))  # a ratio segment set that can no longer be rebuilt would be stale
            del world.segments[mid][dim]
        if mid not in world.recomputed:
            world.recomputed.append(mid)
        changed.add(mid)


def _groups(graph: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """Same-quantity partners (co_measure, twin), supersets of a subset and subsets of a superset."""
    same: dict[str, set[str]] = {}
    sup: dict[str, set[str]] = {}
    sub: dict[str, set[str]] = {}
    for e in graph["edges"]:
        a, b = e["from"], e["to"]
        if e["kind"] in ("co_measure", "twin"):
            same.setdefault(a, set()).add(b)
            same.setdefault(b, set()).add(a)
        elif e["kind"] == "subset":  # from is a subset of to
            sup.setdefault(a, set()).add(b)
            sub.setdefault(b, set()).add(a)
    return same, sup, sub


def _realised(before: dict[date, float], after: dict[date, float], days: Any) -> dict[date, float]:
    """The factor a metric's total actually moved by, per day (a scoped change moves the total by less than its factor)."""
    return {d: after[d] / before[d] for d in days if d in before and d in after and before[d]}


def _follow(world: InjectedWorld, graph: dict[str, Any], definitions: dict[str, Any], metric: str, scope: tuple[str, str] | None,
            factors: dict[date, float], before: dict[date, float], touched: set[str]) -> None:
    """Carry a real change on ``metric`` to its in-fixture partners (additive metrics only; ratios are rebuilt).

    ``before`` is the metric's total series just before this change. A partner with the same segment gets the same
    factor in it; one without gets the total's realised factor, spread over its segments only if the change is everywhere.
    """
    same, sup, sub = _groups(graph)
    realised = _realised(before, world.series[metric], factors)

    def carry(partner: str, partner_factors: dict[date, float], partner_scope: tuple[str, str] | None, spread: bool) -> None:
        partner_before = dict(world.series[partner])
        _scale(world, partner, partner_scope, partner_factors, spread=spread)
        touched.add(partner)
        # The partner is itself a changed metric: its own partners follow, so a group moves as a whole.
        _follow(world, graph, definitions, partner, partner_scope, partner_factors, partner_before, touched)

    for partner in sorted(same.get(metric, ())) + sorted(sub.get(metric, ())):  # a superset's subsets move in proportion
        if partner in world.series and partner not in touched and not _is_ratio(definitions, partner):
            if scope is not None and _has(world, partner, scope):
                carry(partner, factors, scope, False)
            else:
                carry(partner, realised if scope is not None else factors, None, scope is None)
    for parent in sorted(sup.get(metric, ())):  # a subset moved: the superset moves by the subset's delta
        if parent in world.series and parent not in touched and not _is_ratio(definitions, parent):
            base = world.series[parent]
            delta = {d: world.series[metric][d] - before[d] for d in factors if d in before and d in base}
            carry(parent, {d: 1.0 + dv / base[d] for d, dv in delta.items() if base[d]}, None, scope is None)


def _has(world: InjectedWorld, metric: str, scope: tuple[str, str] | None) -> bool:
    return scope is not None and scope[1] in world.segments.get(metric, {}).get(scope[0], {})


def _keep_compositions(world: InjectedWorld, pristine: InjectedWorld, definitions: dict[str, Any], injected: set[str],
                       changed: set[str]) -> None:
    """parent += sum(sign * delta of component), pass by pass, so a pre-existing residual is preserved."""
    for _ in range(COMPOSITION_PASSES):
        for parent, d in sorted(definitions.items()):
            comps = [(c["metric"], int(c["sign"])) for c in (d.get("formula") or {}).get("composition") or []]
            hit = [(c, sg) for c, sg in comps if c in changed and c in world.series]
            if not hit or parent not in world.series:
                continue
            if parent in injected:
                raise InjectionError(f"{parent} and its component {hit[0][0]} are both injected; inject the component only")
            for day in world.series[parent]:
                delta = sum(sg * (world.series[c].get(day, 0.0) - pristine.series[c].get(day, 0.0)) for c, sg in hit)
                world.series[parent][day] = pristine.series[parent][day] + delta
            for dim in list(world.segments.get(parent, {})):
                if all(dim in world.segments.get(c, {}) for c, _ in hit):
                    for seg, row in world.segments[parent][dim].items():
                        for day in row:
                            row[day] = pristine.segments[parent][dim][seg][day] + sum(
                                sg * (world.segments[c][dim].get(seg, {}).get(day, 0.0) - pristine.segments[c][dim].get(seg, {}).get(day, 0.0))
                                for c, sg in hit)
                else:
                    world.dropped.append((parent, dim))
                    del world.segments[parent][dim]
            changed.add(parent)


def apply_injections(fx: W.Fixture, injections: tuple[C.Injection, ...], graph: dict[str, Any]) -> InjectedWorld:
    pristine = InjectedWorld(copy.deepcopy(fx.series), copy.deepcopy(fx.segments))
    world = InjectedWorld(copy.deepcopy(fx.series), copy.deepcopy(fx.segments))
    mech = _edges(graph)
    changed: set[str] = set()
    injected: set[str] = set()
    first_day: date | None = None
    for i, inj in enumerate(injections):
        p = inj.params
        if inj.type not in ("scale", "measurement_break"):
            raise InjectionError(f"unknown injection type {inj.type!r}")
        if inj.onset is None:
            raise InjectionError(f"injection {i} ({inj.type}) needs an onset")
        if not fx.start < inj.onset <= fx.end:
            raise InjectionError(f"injection {i}: onset {inj.onset} outside the fixture window")
        if inj.type == "measurement_break" and p.get("effects"):
            raise InjectionError("a measurement break changes only what is measured: it takes no effects")
        first_day = min(first_day or inj.onset, inj.onset)
        scope_pair = _scope(dict(p.get("scope") or {}))
        until = date.fromisoformat(p["until"]) if p.get("until") else None
        factor, ramp, metric = float(p["factor"]), int(p.get("ramp_days") or 0), str(p["metric"])
        if _is_ratio(fx.definitions, metric):
            raise InjectionError(f"{metric} is a ratio: inject a change in its numerator or denominator")
        if metric not in world.series:
            raise InjectionError(f"metric {metric!r} is not in the fixture")
        days = _days_from(world.series[metric], inj.onset, until)
        factors = {d: _factor_at(d, inj.onset, 0, factor, ramp) for d in days}
        touched = {metric}
        before = dict(world.series[metric])
        _scale(world, metric, scope_pair, factors, spread=scope_pair is None)
        if inj.type == "scale":
            _follow(world, graph, fx.definitions, metric, scope_pair, factors, before, touched)
        # Relative change of each metric on the chain, by day, so the next hop reads it.
        rel = {metric: {d: f - 1.0 for d, f in factors.items()}}
        hops: list[tuple[str, str]] = []
        for eff in p.get("effects") or []:
            src, dst, lag = str(eff["from"]), str(eff["to"]), int(eff.get("lag_days", 0))
            if (src, dst) not in mech:
                raise InjectionError(f"{src} -> {dst} is not a mechanism edge of the graph")
            lo, hi = mech[(src, dst)]
            if not lo <= lag <= hi:
                raise InjectionError(f"{src} -> {dst}: lag {lag} outside the edge's range [{lo}, {hi}]")
            if src not in rel:
                raise InjectionError(f"{src} -> {dst}: {src} was not changed by this injection or an earlier hop")
            if dst not in world.series:
                raise InjectionError(f"{dst} is not in the fixture")
            if dst in touched:
                raise InjectionError(f"{dst} is already changed by this injection (as a partner or an earlier hop)")
            shifted = {d + timedelta(days=lag): r for d, r in rel[src].items()}
            has_seg = _has(world, dst, scope_pair)
            scale_by = 1.0
            if scope_pair is not None and not has_seg:
                # The share of the changed segment in the source metric, falling back to the planted metric.
                holder = src if _has(pristine, src, scope_pair) else metric
                scale_by = _share(pristine, holder, *scope_pair, inj.onset)
            dst_factors = {d: 1.0 + float(eff["elasticity"]) * r * scale_by for d, r in shifted.items() if d in world.series[dst]}
            dst_before = dict(world.series[dst])
            _scale(world, dst, scope_pair if has_seg else None, dst_factors, spread=scope_pair is None)
            touched.add(dst)
            rel[dst] = {d: f - 1.0 for d, f in dst_factors.items()}
            hops.append((src, dst))
            if inj.type == "scale":
                _follow(world, graph, fx.definitions, dst, scope_pair if has_seg else None, dst_factors, dst_before, touched)
        changed |= touched
        injected |= touched
        world.planted.append(Planted(inj.type, metric, inj.onset, dict(p.get("scope") or {}), factor, tuple(hops), inj.type != "measurement_break"))
    if first_day is not None:
        _keep_compositions(world, pristine, fx.definitions, injected, changed)
        _recompute_ratios(world, fx.definitions, changed, first_day)
    return world

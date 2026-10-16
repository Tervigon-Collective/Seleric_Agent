"""The gather step (plan P2.6, D-46): from a frozen ``GatherPlan`` and a ``SeriesPort`` to the engine input.

Everything here was in the tool's ``_diagnose`` between the plan and the engine; it moved unchanged in behaviour, with
the catalogue reads replaced by the plan and the semantic layer replaced by the port, so it runs on a recorded run
exactly as it ran live. The three fetch phases keep their order and their overlap (the outcome's segments are fetched
beside the series), and the rules about a metric that cannot take the question's scope are the ones the tool always had:
a series that describes another scope is left out, never used as if it were this one."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from datetime import date
from typing import Any

from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.gather import Gathered, GatherPlan
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import Series
from seleric_swarm.diagnosis.domain.subgraph import COMEASURE_KIND
from seleric_swarm.diagnosis.domain.types import Scope, SeriesRequest, SeriesResult, Window
from seleric_swarm.diagnosis.operators.identities import discover_identities, discover_rate_chains
from seleric_swarm.diagnosis.ports.series import SeriesPort

# A named stage of the gather, with what it is about; the tool turns it into a trace scope, a replay ignores it.
Scoped = Callable[[str, dict[str, Any]], AbstractContextManager[Any]]
Phase = Callable[[str], None]


def _no_scope(name: str, inputs: dict[str, Any]) -> AbstractContextManager[Any]:
    return nullcontext()


def rate_parts(
    metric_id: str, lineage: dict[str, MetricMeta] | Any, series: dict[str, Series], days: list[date], *, cfg: DiagnosisConfig
) -> tuple[str | None, str | None]:
    """The volume a rate is averaged over: its denominator, as the data verify it.

    Lineage lists a ratio's components but not which one divides; weighting the
    segments by the numerator (clicks for a click-through rate) mixes the
    segments on the wrong volume and breaks the mix/rate split. A verified
    ``outcome = a / b`` identity names ``b``; a one-component rate is "per" that
    component. The numerator comes back only from a verified ``a / b``.
    """
    meta = lineage.get(metric_id, MetricMeta(metric_id))
    # Components may live in other views (spend and sales of a return on spend);
    # the engine only trusts a split whose segments rebuild the total on the data.
    additive = {d for d in meta.depends_on if lineage.get(d, MetricMeta(d)).additive}
    for ident in discover_identities(metric_id, lineage, series, days, cfg=cfg):
        den = [m for m, e in ident.factors if e < 0]
        num = [m for m, e in ident.factors if e > 0]
        if len(ident.factors) == 2 and len(den) == 1 and den[0] in additive:
            return (num[0] if num and num[0] in additive else None), den[0]
    if len(meta.depends_on) == 1 and meta.depends_on[0] in additive:
        return None, meta.depends_on[0]
    return None, None


def _requests(
    jobs: Sequence[tuple[str, str | None]], window: Window, scope: Scope
) -> list[SeriesRequest]:
    return [SeriesRequest(metric, window, scope, dimension) for metric, dimension in jobs]


async def gather(
    plan: GatherPlan,
    series_port: SeriesPort,
    *,
    cfg: DiagnosisConfig,
    scoped: Scoped = _no_scope,
    phase: Phase = lambda name: None,
) -> Gathered:
    window = Window(plan.start, plan.end)
    scope = Scope.from_filters(plan.scope)
    outcome = plan.outcome
    lineage = dict(plan.lineage)
    gathered = Gathered(input=None)

    async def segments_beside_series() -> list[SeriesResult]:
        with scoped("fetch_segments", {"jobs": len(plan.dims), "beside": "fetch_series"}):
            return await series_port.fetch(_requests([(outcome, d) for d in plan.dims], window, scope))

    # An additive outcome's segment queries need only the dimensions, so they run beside the series fetch.
    early: asyncio.Future[list[SeriesResult]] | None = (
        asyncio.ensure_future(segments_beside_series()) if plan.outcome_additive else None
    )
    with scoped("fetch_series", {"metrics": len(plan.series_ids)}):
        series_results = await series_port.fetch(_requests([(m, None) for m in plan.series_ids], window, scope))
    phase("fetch_series")

    bridge_pool = list(plan.bridge_pool)
    series: dict[str, Series] = {}
    for m, result in zip(plan.series_ids, series_results, strict=True):
        if result.error is not None:
            if m == outcome:
                if early is not None:
                    early.cancel()
                gathered.failure, gathered.failure_detail = "fetch_failed", result.error
                return gathered
            gathered.notes.append(f"{m}: fetch failed ({result.error[:120]})")
            continue
        if result.dropped_filters:
            dropped = ", ".join(result.dropped_filters)
            if m in plan.change_log or (m in plan.ladder_ends and m not in bridge_pool):
                # Not recorded for this scope: a stage or an edit count for the whole account describes another scope.
                gathered.notes.append(f"{m} is not recorded by {dropped}, so it is left out of this scope")
                continue
            if m in bridge_pool and m not in plan.core:
                # An unfiltered total cannot be a term of a filtered outcome.
                bridge_pool.remove(m)
                continue
            if m != outcome:
                # A whole-account figure beside a filtered outcome describes another scope: the account's web
                # purchases were reported as one campaign's (live 2026-10-10 thread_066b9cd1).
                gathered.notes.append(f"{m} is not recorded by {dropped}, so it is left out of this scope")
                continue
            gathered.notes.append(f"{m} cannot be filtered by {dropped}; used unfiltered")
        if result.values:
            series[m] = dict(result.values)
            gathered.series_args[m] = result.query_args
            if result.currency:
                gathered.units[m] = result.currency
    if outcome not in series:
        if early is not None:
            early.cancel()
        gathered.failure = "no_data"
        return gathered

    ev_start = min(plan.event_days)
    history_days = sorted(d for d in series[outcome] if d < ev_start and d not in plan.partial_days)
    numerator, weight = (None, None) if plan.outcome_additive else rate_parts(outcome, lineage, series, history_days, cfg=cfg)
    seg_jobs = [(outcome, d) for d in plan.dims] + [(m, d) for m in (weight, numerator) if m for d in plan.dims]
    if early is not None:
        seg_results = await early
    else:
        with scoped("fetch_segments", {"jobs": len(seg_jobs)}):
            seg_results = await series_port.fetch(_requests(seg_jobs, window, scope))
    phase("fetch_segments")
    segments: dict[str, dict[str, dict[str, Series]]] = {}
    collect = _Segments(segments, gathered, plan.max_segment_rows)
    for (m, d), result in zip(seg_jobs, seg_results, strict=True):
        collect.add(m, d, result)

    # Segments for every chain rate the data actually verify (the engine picks which component leads; fetching for
    # each verified chain avoids re-implementing that choice here).
    targets = [outcome] if plan.outcome_additive else []
    for ident in discover_identities(outcome, lineage, series, history_days, cfg=cfg)[:1]:
        targets += [m for m, _ in ident.factors if lineage.get(m, MetricMeta(m)).additive]
    chain_pairs: list[tuple[str, str]] = []
    for t in dict.fromkeys(targets):
        for ident in discover_rate_chains(t, lineage, series, history_days, cfg=cfg)[:1]:
            chain_pairs.append((ident.factors[0][0], ident.factors[1][0]))
    dims_of = {(rate, base): dims for rate, base, dims in plan.chain_dims}
    jobs_b: list[tuple[str, str]] = []
    for rate_m, base_m in dict.fromkeys(chain_pairs):
        for d in dims_of.get((rate_m, base_m), ()):
            jobs_b += [(rate_m, d), (base_m, d)]
    jobs_b = [j for j in dict.fromkeys(jobs_b) if j not in gathered.segment_args]
    with scoped("fetch_chain_segments", {"jobs": len(jobs_b)}):
        results_b = await series_port.fetch(_requests(jobs_b, window, scope))
    phase("fetch_chain_segments")
    for (m, d), result in zip(jobs_b, results_b, strict=True):
        collect.add(m, d, result)

    gathered.history_days, gathered.weight, gathered.numerator = history_days, weight, numerator
    offered = [d for d in plan.drivers if d in series]
    gathered.input = DiagnosisInput(
        outcome=outcome, event_days=list(plan.event_days), series=series, lineage=lineage, segments=segments,
        candidate_drivers=offered, partial_days=set(plan.partial_days),
        claimed_direction=plan.claimed_direction, denominators=({outcome: weight} if weight else {}),
        numerators=({outcome: numerator} if numerator and weight else {}),
        scope_tokens=plan.scope_tokens, baseline_days=list(plan.baseline_days),
        bridge_candidates=[m for m in bridge_pool if m in series],
        driver_paths=_driver_paths(plan, offered, series), candidates_from_graph=plan.drivers_from_graph,
        driver_chains=_driver_chains(plan, offered), estimate_cap=plan.estimate_cap,
        measurement_pairs=_measurement_pairs(plan, series),
    )
    return gathered


def _driver_paths(plan: GatherPlan, offered: Sequence[str], series: dict[str, Series]) -> dict[str, list[str]]:
    """What each offered driver's effect must pass through (the plan's subgraph), keeping the metrics that have data."""
    if plan.subgraph is None:
        return {}
    by_metric = {c.metric: c for c in plan.subgraph.candidates}
    paths = {d: [m for m in by_metric[d].intermediates if m in series] for d in offered if d in by_metric}
    return {d: p for d, p in paths.items() if p}


class _Segments:
    """Collects the split series a segment fetch returned: a split needs at least two segments, and an answer of more
    rows than the plan allows is a dimension that does not group (an id), not a split."""

    def __init__(self, into: dict[str, dict[str, dict[str, Series]]], gathered: Gathered, max_rows: int) -> None:
        self._into, self._gathered, self._max_rows = into, gathered, max_rows

    def add(self, metric: str, dimension: str, result: SeriesResult) -> None:
        if result.error is not None or result.rows > self._max_rows:
            return
        parsed = {segment: dict(values) for segment, values in result.segments.items()}
        if len(parsed) >= 2:
            self._into.setdefault(metric, {})[dimension] = parsed
            self._gathered.segment_args[(metric, dimension)] = result.query_args


def _measurement_pairs(plan: GatherPlan, series: dict[str, Series]) -> list[tuple[str, str, str]]:
    """The co-measure pairs of the outcome and its parts (the plan's subgraph) with data on both sides."""
    if plan.subgraph is None:
        return []
    return [
        (p.metric, p.peer, p.expected) for p in plan.subgraph.peers
        if p.kind == COMEASURE_KIND and p.metric in series and p.peer in series
    ]


def _driver_chains(plan: GatherPlan, offered: Sequence[str]) -> dict[str, list[list[Any]]]:
    """For each graph-chosen driver offered, the reviewed edges from it to the outcome's part (to be confirmed on the data)."""
    if plan.subgraph is None or not plan.drivers_from_graph:
        return {}
    specs = {(e.source, e.target): e for e in plan.subgraph.edges}
    by_metric = {c.metric: c for c in plan.subgraph.candidates}
    return {
        d: [[e.source, e.target, e.sign, e.lag_days[0], e.lag_days[1], e.confidence] for e in (specs[k] for k in by_metric[d].chain)]
        for d in offered if d in by_metric
    }

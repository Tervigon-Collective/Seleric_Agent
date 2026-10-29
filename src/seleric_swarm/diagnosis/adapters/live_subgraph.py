"""The candidate subgraph for a live diagnosis (plan P3.1): the graph checked against the live catalogue, walked from
the outcome, with the data source's own limits (a metric the catalogue lacks, one the engine cannot estimate, one the
decomposition already reports) as the ``usable`` test. The result is frozen in the ``GatherPlan``; a failure to build
it is reported (trace and log) and the diagnosis goes on without it."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping
from typing import Any

from seleric_swarm.diagnosis.adapters.legacy_trace import LegacyTrace
from seleric_swarm.diagnosis.domain.errors import GraphRejected
from seleric_swarm.diagnosis.domain.graph import LoadedGraph
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.subgraph import COMEASURE_KIND, Subgraph
from seleric_swarm.diagnosis.graph.live import build_live_graph
from seleric_swarm.diagnosis.graph.subgraph import build_subgraph
from seleric_swarm.diagnosis.trace.active import report_failure

_log = logging.getLogger("seleric.diagnosis.trace")


def usable_from_lineage(
    lineage: Mapping[str, MetricMeta], arithmetic_parts: Collection[str], has_metric: Callable[[str], bool]
) -> Callable[[str], str | None]:
    """Why a metric cannot be offered as a cause, or None when it can: not in the catalogue, not a quantity the engine
    estimates (additive), or a part of the outcome's arithmetic that the decomposition reports."""

    def usable(metric: str) -> str | None:
        meta = lineage.get(metric)
        if meta is None or not has_metric(metric):
            return "not_in_catalogue"
        if not meta.additive:
            return "not_additive"
        if metric in arithmetic_parts:
            return "arithmetic_part_of_outcome"
        return None

    return usable


def graph_for(definitions: Mapping[str, Mapping[str, Any]], trace: LegacyTrace | None) -> LoadedGraph | None:
    """The graph checked against the live catalogue: through the live trace when there is one (it shows what was set
    aside), directly otherwise. None when the graph is rejected."""
    if trace is not None:
        return trace.graph(definitions)
    try:
        return build_live_graph(definitions)
    except GraphRejected:
        _log.error("graph_rejected", exc_info=True)
        return None


def plan_subgraph(
    loaded: LoadedGraph | None, outcome: str, lineage: Mapping[str, MetricMeta], arithmetic_parts: Collection[str],
    has_metric: Callable[[str], bool],
) -> Subgraph | None:
    if loaded is None:
        return None  # the graph was rejected; that is already on the trace
    if loaded.graph.node(outcome) is None:
        return None  # a metric the reviewed graph does not cover: the caller falls back and says so
    try:
        return build_subgraph(loaded.graph, outcome, usable=usable_from_lineage(lineage, arithmetic_parts, has_metric))
    except Exception as exc:
        _log.error("subgraph_not_built outcome=%s", outcome, exc_info=True)
        report_failure("build_subgraph", exc, {"outcome": outcome})
        return None


def subgraph_drivers(subgraph: Subgraph, cap: int) -> list[str]:
    """The candidates the engine is given, in the order the subgraph offers them, at most ``cap`` of them."""
    return [c.metric for c in subgraph.candidates[:cap]]


def subgraph_path_metrics(subgraph: Subgraph | None, drivers: Collection[str]) -> list[str]:
    """The metrics the offered drivers' effects must pass through, so their series are fetched too."""
    if subgraph is None:
        return []
    return list(dict.fromkeys(m for c in subgraph.candidates if c.metric in drivers for m in c.intermediates))


MAX_PEERS = 12  # co-measure peers fetched beside the series: the outcome's own first, then its parts'


def subgraph_peer_metrics(subgraph: Subgraph | None, has_metric: Callable[[str], bool]) -> list[str]:
    """The other measurements of the outcome and its parts (co-measure pairs) whose series are fetched for the check
    that compares them. Twins differ legitimately day to day (another date basis) and are not compared."""
    if subgraph is None:
        return []
    order = {m: i for i, m in enumerate((subgraph.outcome, *subgraph.accounting))}
    peers = sorted((p for p in subgraph.peers if p.kind == COMEASURE_KIND and has_metric(p.peer)), key=lambda p: (order.get(p.metric, 99), p.peer))
    return list(dict.fromkeys(m for p in peers for m in (p.metric, p.peer)))[:MAX_PEERS]


POOL_EXTRA = 8  # graph candidates fetched beyond the estimation cap, so unconfirmed ones can be replaced by the next in line


def same_quantity_of_outcome(subgraph: Subgraph | None, metrics: list[str]) -> list[str]:
    """``metrics`` without any that is another measurement of the outcome or of a part of it (twin, co-measure, subset,
    allocation): the rule the graph's candidates obey, applied to a list the catalogue's lineage proposed."""
    if subgraph is None:
        return metrics
    same = {p.peer for p in subgraph.peers}
    return [m for m in metrics if m not in same]


def declared_bridge_pool(loaded: LoadedGraph, outcome: str) -> list[str]:
    """The metrics an accounting bridge of ``outcome`` may use: the terms of its declared composition, every level down,
    or, when it has none, those of a metric the reviewed pairs say measures the same quantity (net sales has no formula;
    the P&L's net sales, its co-measure, has seven terms). The data still decide (the bridge must reproduce the outcome on
    every history day). Allocated shares and the wholes built from the outcome are never terms: ad spend is not
    'attributed contribution minus net profit' (live 2026-10-29). Empty when nothing declares how the outcome adds up."""
    from seleric_swarm.diagnosis.domain.graph import EdgeKind

    g = loaded.graph
    parts: dict[str, list[str]] = {}
    peers: list[str] = []
    for e in g.edges:
        if e.kind == EdgeKind.COMPOSITION:
            parts.setdefault(e.target, []).append(e.source)
        elif e.kind in (EdgeKind.CO_MEASURE, EdgeKind.TWIN) and outcome in (e.source, e.target):
            peers.append(e.target if e.source == outcome else e.source)
    roots = [outcome] if outcome in parts else [p for p in dict.fromkeys(peers) if p in parts]
    pool: list[str] = []
    todo = [t for r in roots for t in parts[r]]
    while todo:
        m = todo.pop(0)
        node = g.nodes.get(m)
        if m == outcome or m in pool or node is None or node.allocated:
            continue
        pool.append(m)
        todo += parts.get(m, [])
    return pool

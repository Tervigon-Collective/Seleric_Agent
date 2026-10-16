"""The candidate subgraph for a live diagnosis (plan P3.1): the graph checked against the live catalogue, walked from
the outcome, with the data source's own limits (a metric the catalogue lacks, one the engine cannot estimate, one the
decomposition already reports) as the ``usable`` test. The result is frozen in the ``GatherPlan``; a failure to build
it is reported (trace and log) and the diagnosis goes on without it."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping
from typing import Any

from seleric_swarm.diagnosis.adapters.catalogue_graph import load_against_catalogue
from seleric_swarm.diagnosis.adapters.legacy_trace import LegacyTrace
from seleric_swarm.diagnosis.domain.errors import GraphRejected
from seleric_swarm.diagnosis.domain.graph import LoadedGraph
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.subgraph import Subgraph
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
        return load_against_catalogue(definitions)
    except GraphRejected:
        _log.error("graph_rejected", exc_info=True)
        return None


def plan_subgraph(
    loaded: LoadedGraph | None, outcome: str, lineage: Mapping[str, MetricMeta], arithmetic_parts: Collection[str],
    has_metric: Callable[[str], bool],
) -> Subgraph | None:
    if loaded is None:
        return None  # the graph was rejected; that is already on the trace
    try:
        return build_subgraph(loaded.graph, outcome, usable=usable_from_lineage(lineage, arithmetic_parts, has_metric))
    except Exception as exc:
        _log.error("subgraph_not_built outcome=%s", outcome, exc_info=True)
        report_failure("build_subgraph", exc, {"outcome": outcome})
        return None

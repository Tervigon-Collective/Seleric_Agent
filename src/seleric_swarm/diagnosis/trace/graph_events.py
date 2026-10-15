"""A loaded graph, as trace events: what was loaded and everything the loader set aside (plan P1.3 / P1.5)."""

from __future__ import annotations

from seleric_swarm.diagnosis.domain.graph import LoadedGraph
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.emitter import Emitter

# Issue codes that exclude a metric from the investigation; every other code is a graph-level fact.
_EXCLUSIONS = frozenset({"node_not_in_catalogue", "node_definition_drift"})


def emit_graph_loaded(emitter: Emitter, loaded: LoadedGraph, *, parent: int | None = None) -> None:
    graph = loaded.graph
    summary = emitter.emit(
        kinds.GRAPH_LOADED,
        {
            "graph_version": graph.version, "catalogue_version": graph.catalogue_version,
            "nodes": len(graph.nodes), "edges": len(graph.edges), "issues": len(loaded.issues),
        },
        parent=parent,
    )
    for issue in loaded.issues:
        if issue.code in _EXCLUSIONS:
            emitter.emit(
                kinds.EXCLUSION,
                {"metric": issue.subject, "reason_code": issue.code, "check_value": issue.detail},
                parent=summary.seq,
            )
        else:
            emitter.emit(
                kinds.GRAPH_ISSUE,
                {"code": issue.code, "subject": issue.subject, "detail": issue.detail},
                parent=summary.seq,
            )

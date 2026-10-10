"""Build the metric graph from the read model plus a parsed causal-graph config."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from seleric_swarm.graph.model import Edge, MetricGraph, SkippedNode
from seleric_swarm.semantic import SemanticReadModel


def identity_edges(read_model: SemanticReadModel) -> list[Edge]:
    """Component → composite edges from each metric's ``composition`` / ``depends_on``."""
    known = read_model.metric_ids()
    out: list[Edge] = []
    for m in read_model.metrics:
        for source, role, coefficient in _components(m.composition, m.extra.get("depends_on")):
            if source in known and source != m.id:
                out.append(Edge(source=source, target=m.id, kind="identity", role=role, coefficient=coefficient))
    return list({(e.source, e.target, e.role): e for e in out}.values())


def _components(composition: Any, depends_on: Any) -> list[tuple[str, str, float | None]]:
    out: list[tuple[str, str, float | None]] = []
    terms: Any = composition
    if isinstance(composition, Mapping):
        for role in ("numerator", "denominator"):
            if isinstance(composition.get(role), str):
                out.append((composition[role], role, None))
        terms = composition.get("terms") or composition.get("components")
        depends_on = depends_on or composition.get("depends_on")
    if isinstance(terms, (list, tuple)):
        for term in terms:
            if isinstance(term, Mapping) and isinstance(term.get("metric"), str):
                coef = term.get("coefficient", 1.0)
                out.append((term["metric"], "term", float(coef) if isinstance(coef, (int, float)) else None))
            elif isinstance(term, str):
                out.append((term, "term", 1.0))
    if isinstance(depends_on, (list, tuple)):
        out += [(d, "depends_on", None) for d in depends_on if isinstance(d, str)]
    return out


def causal_edges(
    config: Mapping[str, Any] | None, read_model: SemanticReadModel
) -> tuple[list[Edge], list[SkippedNode]]:
    """Edges from ``{"graphs": [{"id", "edges": [[cause, effect] | {source, target, sign, lag_days}]}]}``.

    A node must be a catalogue metric or dimension id; anything else is skipped and reported
    (semantic pickup: renaming a metric in the catalogue drops its edges instead of breaking).
    """
    known = read_model.metric_ids() | {d.id for d in read_model.dimensions}
    edges: list[Edge] = []
    skipped: dict[tuple[str, str], SkippedNode] = {}
    for graph in (config or {}).get("graphs") or []:
        if not isinstance(graph, Mapping):
            continue
        gid = str(graph.get("id") or "")
        for raw in graph.get("edges") or []:
            parsed = _edge(raw)
            if parsed is None:
                continue
            source, target, sign, lag = parsed
            missing = [n for n in (source, target) if n not in known]
            for n in missing:
                skipped[(gid, n)] = SkippedNode(node=n, graph_id=gid, reason="not_in_catalogue")
            if not missing:
                edges.append(Edge(source=source, target=target, kind="causal", sign=sign, lag_days=lag, graph_id=gid))
    return edges, list(skipped.values())


def _edge(raw: Any) -> tuple[str, str, Literal[1, -1, 0], int] | None:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        source, target = raw[0], raw[1]
        sign = raw[2] if len(raw) > 2 else 0
        lag = raw[3] if len(raw) > 3 else 0
    elif isinstance(raw, Mapping):
        source, target = raw.get("source"), raw.get("target")
        sign, lag = raw.get("sign", 0), raw.get("lag_days", 0)
    else:
        return None
    if not isinstance(source, str) or not isinstance(target, str):
        return None
    sign_i: Literal[1, -1, 0] = 1 if sign == 1 else -1 if sign == -1 else 0
    return source, target, sign_i, int(lag) if isinstance(lag, int) and lag >= 0 else 0


def build_graph(read_model: SemanticReadModel, causal_config: Mapping[str, Any] | None = None) -> MetricGraph:
    causal, skipped = causal_edges(causal_config, read_model)
    return MetricGraph(
        catalogue_version=read_model.version,
        edges=tuple(identity_edges(read_model) + causal),
        skipped=tuple(skipped),
    )

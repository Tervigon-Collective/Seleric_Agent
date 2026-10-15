"""Turn the graph document into a validated ``Graph`` (plan P1.3).

Two kinds of failure, kept apart on purpose:

* The document cannot be trusted at all (unknown schema, a node that is not a node, edge totals that disagree
  with the document's own counts, builder-reported errors): ``GraphRejected``.
* One part is unusable (an edge of an unknown kind, an edge to a metric that does not exist, a metric whose
  definition has moved in the live catalogue): that part is left out and a ``GraphIssue`` says so. The rest of
  the graph stays usable, and nothing is dropped silently.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from seleric_swarm.diagnosis.domain.canonical import fingerprint
from seleric_swarm.diagnosis.domain.errors import GraphRejected
from seleric_swarm.diagnosis.domain.graph import (
    CatalogueFacts,
    Direction,
    Edge,
    EdgeKind,
    Graph,
    GraphIssue,
    Hierarchy,
    LoadedGraph,
    Node,
    Ratio,
    Tier,
)

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
STATUS_BROKEN = "broken"

_NODE_STRINGS = ("id", "label", "view", "category", "aggregation", "unit", "grain", "date_axis", "status", "formula")


def _reject(code: str, detail: str) -> GraphRejected:
    return GraphRejected(code, detail)


def _mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _reject("malformed", f"{what} is not an object")
    return value


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise TypeError("expected a list of strings")
    return tuple(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


def _parse_node(raw: Any) -> Node:
    doc = _mapping(raw, "a node")
    for key in _NODE_STRINGS:
        if not isinstance(doc.get(key), str):
            raise _reject("malformed_node", f"node {doc.get('id')!r}: {key} is missing or not text")
    tier = doc.get("tier")
    if isinstance(tier, bool) or not isinstance(tier, int):
        raise _reject("malformed_node", f"node {doc['id']!r}: tier is not an integer")
    ratio_doc = doc.get("ratio")
    ratio: Ratio | None = None
    if ratio_doc is not None:
        parts = _mapping(ratio_doc, f"ratio of node {doc['id']!r}")
        numerator, denominator = parts.get("numerator"), parts.get("denominator")
        if not isinstance(numerator, str) or not isinstance(denominator, str):
            raise _reject("malformed_node", f"node {doc['id']!r}: ratio needs a numerator and a denominator")
        ratio = Ratio(numerator, denominator)
    try:
        group = _strings(doc.get("same_quantity_group"))
    except (ValueError, TypeError) as exc:
        raise _reject("malformed_node", f"node {doc['id']!r}: same_quantity_group: {exc}") from exc
    return Node(
        id=doc["id"], label=doc["label"], view=doc["view"], category=doc["category"],
        aggregation=doc["aggregation"], unit=doc["unit"], grain=doc["grain"], date_axis=doc["date_axis"],
        status=doc["status"], tier=tier, allocated=bool(doc.get("allocated")), formula=doc["formula"],
        ratio=ratio, same_quantity_group=group,
    )


def _parse_lag(value: Any) -> tuple[int, int]:
    if (
        not isinstance(value, (list, tuple)) or len(value) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        raise ValueError(f"lag_days must be two integers, got {value!r}")
    low, high = int(value[0]), int(value[1])
    if low < 0 or high < low:
        raise ValueError(f"lag_days range is not ordered or is negative: {value!r}")
    return low, high


def _parse_edge(raw: Mapping[str, Any]) -> Edge:
    """One edge, or ``ValueError`` saying why this edge alone is unusable."""
    kind = EdgeKind(raw["kind"])  # ValueError for a kind this engine does not know
    source, target = raw.get("from"), raw.get("to")
    if not isinstance(source, str) or not isinstance(target, str):
        raise TypeError("edge has no from/to")
    basis, confidence = raw.get("basis"), raw.get("confidence")
    if not isinstance(basis, str) or not isinstance(confidence, str):
        raise TypeError("edge has no basis/confidence")
    coefficient: int | None = None
    direction: Direction | None = None
    lag: tuple[int, int] | None = None
    if kind is EdgeKind.COMPOSITION:
        sign = raw.get("sign")
        if sign not in (1, -1) or isinstance(sign, bool):
            raise ValueError(f"composition sign must be 1 or -1, got {sign!r}")
        coefficient = int(sign)
    if kind in (EdgeKind.MECHANISM, EdgeKind.FEEDBACK):
        direction = Direction(str(raw.get("sign")))
        lag = _parse_lag(raw.get("lag_days"))
    return Edge(
        source=source, target=target, kind=kind, basis=basis, confidence=confidence, coefficient=coefficient,
        direction=direction, lag_days=lag, role=_optional_str(raw.get("role")),
        verified_by=_optional_str(raw.get("verified_by")), twin_of=_strings(raw.get("twin_of")),
        systems=_strings(raw.get("systems")), expected=_optional_str(raw.get("expected")),
        note=_optional_str(raw.get("note")), rate=_optional_str(raw.get("rate")), key=_optional_str(raw.get("key")),
    )


def _parse_tiers(raw: Any) -> dict[int, Tier]:
    out: dict[int, Tier] = {}
    for key, value in _mapping(raw, "tiers").items():
        doc = _mapping(value, f"tier {key}")
        try:
            tier_id = int(key)
        except (TypeError, ValueError) as exc:
            raise _reject("malformed", f"tier key {key!r} is not an integer") from exc
        out[tier_id] = Tier(tier_id, str(doc.get("name", "")), str(doc.get("note", "")))
    return out


def _parse_hierarchies(raw: Any) -> dict[str, Hierarchy]:
    out: dict[str, Hierarchy] = {}
    for name, value in _mapping(raw, "where.hierarchies").items():
        doc = _mapping(value, f"hierarchy {name}")
        try:
            out[str(name)] = Hierarchy(str(name), _strings(doc.get("levels")), _strings(doc.get("views")))
        except (ValueError, TypeError) as exc:
            raise _reject("malformed", f"hierarchy {name}: {exc}") from exc
    return out


def _drift(node: Node, facts: CatalogueFacts) -> list[str]:
    pairs = (
        ("aggregation", node.aggregation, facts.aggregation), ("unit", node.unit, facts.unit),
        ("grain", node.grain, facts.grain), ("view", node.view, facts.view),
    )
    return [f"{name}: graph {had!r}, catalogue {now!r}" for name, had, now in pairs if had != now]


def _renamed(document: Mapping[str, Any], renames: Mapping[str, str]) -> dict[str, Any]:
    """The document with metric ids replaced everywhere an id appears (Cube v3 renames metrics; the graph is
    regenerated later, this bridges until then). Two ids mapped to one would merge metrics: refused."""
    targets = list(renames.values())
    if len(set(targets)) != len(targets):
        raise _reject("rename_collision", "two metrics are renamed to the same id")

    def one(value: Any) -> Any:
        return renames.get(value, value) if isinstance(value, str) else value

    def many(value: Any) -> Any:
        return [one(v) for v in value] if isinstance(value, list) else value

    doc = dict(document)
    nodes = []
    for raw in doc.get("nodes") or []:
        node = dict(_mapping(raw, "a node"))
        node["id"] = one(node.get("id"))
        node["same_quantity_group"] = many(node.get("same_quantity_group"))
        if isinstance(node.get("ratio"), Mapping):
            node["ratio"] = {k: one(v) for k, v in node["ratio"].items()}
        nodes.append(node)
    edges = []
    for raw in doc.get("edges") or []:
        edge = dict(_mapping(raw, "an edge"))
        for key in ("from", "to"):
            edge[key] = one(edge.get(key))
        for key in ("twin_of",):
            if key in edge:
                edge[key] = many(edge[key])
        edges.append(edge)
    doc["nodes"], doc["edges"] = nodes, edges
    return doc


def load_graph(
    document: Mapping[str, Any],
    *,
    live: Mapping[str, CatalogueFacts] | None = None,
    catalogue_version: str = "",
    renames: Mapping[str, str] | None = None,
) -> LoadedGraph:
    """Validate ``document`` and, when ``live`` is given, check every metric against the live catalogue.

    ``renames`` maps ids in the document to the ids the catalogue now uses; the graph version stays the digest of
    the document as it was written."""
    version = fingerprint(dict(document))
    doc = _mapping(_renamed(document, renames) if renames else document, "the graph document")
    schema = doc.get("version")
    if schema not in SUPPORTED_SCHEMA_VERSIONS:
        raise _reject("unsupported_schema", f"graph schema version {schema!r}, supported {sorted(SUPPORTED_SCHEMA_VERSIONS)}")
    for key in ("nodes", "edges", "tiers", "counts", "edge_kinds"):
        if key not in doc:
            raise _reject("malformed", f"graph document has no {key!r}")
    validation = _mapping(doc.get("validation") or {}, "validation")
    errors = validation.get("errors") or []
    if errors:
        raise _reject("builder_errors", f"the graph builder reported errors: {errors}")
    if live is not None and not live:
        raise _reject("catalogue_empty", "the live catalogue returned no metrics to check the graph against")

    issues: list[GraphIssue] = [
        GraphIssue("builder_warning", "graph", str(w)) for w in (validation.get("warnings") or [])
    ]

    raw_nodes, raw_edges = doc["nodes"], doc["edges"]
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise _reject("malformed", "nodes and edges must be lists")
    nodes: dict[str, Node] = {}
    for raw in raw_nodes:
        node = _parse_node(raw)
        if node.id in nodes:
            raise _reject("duplicate_node", f"metric {node.id!r} appears twice")
        nodes[node.id] = node

    if not all(isinstance(e, Mapping) for e in raw_edges):
        raise _reject("malformed", "an edge is not an object")
    counted = Counter(str(e.get("kind")) for e in raw_edges)
    declared = {str(k): v for k, v in _mapping(doc["counts"], "counts").items()}
    if len(raw_edges) != sum(counted.values()) or dict(counted) != declared:
        raise _reject("edge_counts_disagree", f"document declares {declared}, edges contain {dict(counted)}")

    edges: list[Edge] = []
    for raw in raw_edges:
        label = f"{raw.get('from')}->{raw.get('to')} ({raw.get('kind')})"
        try:
            edge = _parse_edge(raw)
        except (ValueError, TypeError, KeyError) as exc:
            code = "unknown_edge_kind" if str(raw.get("kind")) not in {k.value for k in EdgeKind} else "malformed_edge"
            issues.append(GraphIssue(code, label, str(exc)))
            continue
        missing = [m for m in (edge.source, edge.target) if m not in nodes]
        if missing:
            issues.append(GraphIssue("edge_unknown_node", label, f"no such metric: {missing}"))
            continue
        edges.append(edge)

    for node in nodes.values():
        if node.status == STATUS_BROKEN:
            issues.append(GraphIssue("node_status_broken", node.id, "the catalogue marks this metric broken"))

    excluded: list[str] = []
    if live is not None:
        for node in nodes.values():
            facts = live.get(node.id)
            if facts is None:
                issues.append(GraphIssue("node_not_in_catalogue", node.id, "the live catalogue has no such metric"))
                excluded.append(node.id)
                continue
            moved = _drift(node, facts)
            if moved:
                issues.append(GraphIssue("node_definition_drift", node.id, "; ".join(moved)))
                excluded.append(node.id)
    gone = set(excluded)
    if gone:
        edges = [e for e in edges if e.source not in gone and e.target not in gone]
        nodes = {
            k: replace(v, same_quantity_group=tuple(m for m in v.same_quantity_group if m not in gone))
            for k, v in nodes.items() if k not in gone
        }

    where = _mapping(doc.get("where") or {}, "where")
    graph = Graph(
        schema_version=int(schema), version=version,
        catalogue_version=catalogue_version if live is not None else "",
        nodes=nodes, edges=tuple(edges), tiers=_parse_tiers(doc["tiers"]),
        hierarchies=_parse_hierarchies(where.get("hierarchies") or {}),
        edge_kind_notes={str(k): str(v) for k, v in _mapping(doc["edge_kinds"], "edge_kinds").items()},
    )
    return LoadedGraph(graph, tuple(issues), tuple(excluded))

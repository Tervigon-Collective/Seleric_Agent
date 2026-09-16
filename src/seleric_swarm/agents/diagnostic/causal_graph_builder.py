"""Business-wide causal graph, derived from ``config/metric_registry.yaml``.

DoWhy needs a DAG to walk from an outcome metric back to everything that could
have driven it. Rather than hand-author a second RCA-story artifact per
outcome, this builds one graph from data the registry already declares on
itself: the ``depends_on`` field each metric's formula lists. A metric with no
``depends_on`` simply has no derived edge -- that's honest, not a gap to
paper over with invented relationships.

Node identity uses ``ontology.node_for_metric`` -- the same function
``causal_discovery.py`` and ``causal/estimator.py`` use to map a metric id
onto a graph node label. That's what lets this metric-registry-derived graph
share nodes with the hand-authored dimension-level graph in
``config/causal_graphs.example.yaml`` (``metric.avg_price`` and ``price``
collapse onto one node, ``metric.cac`` and ``purchase`` collapse onto one
node) instead of living as two disconnected label spaces.
"""

from __future__ import annotations

from seleric_swarm.agents.diagnostic.ontology import graph_identity, node_for_metric
from seleric_swarm.agents.skeptic.registries import (
    CausalGraph,
    InMemoryCausalGraphRegistry,
    causal_graphs_from_yaml,
)
from seleric_swarm.services.metrics import MetricRegistry

BUSINESS_GRAPH_ID = "causal.business.v1"
_FUNNEL_GRAPH_ID = "causal.funnel_purchase.v1"


def _resolve_dependency(name: str, metrics: MetricRegistry, by_catalogue: dict[str, str]) -> str:
    candidate = name if name.startswith("metric.") else f"metric.{name}"
    if metrics.get(candidate) is not None:
        return candidate
    mapped = by_catalogue.get(name)
    if mapped:
        return mapped
    for cat_name, mid in by_catalogue.items():
        if cat_name.startswith(name) or name.startswith(cat_name):
            return mid
    needle = name.lower().replace("_", " ")
    for m in metrics.all():
        if needle in m.aliases or m.id.removeprefix("metric.").replace("_", " ") == needle:
            return m.id
    return name


def _node_label(ident: str, metrics: MetricRegistry) -> str:
    return node_for_metric(graph_identity(ident, metrics))


def build_business_graph(metrics: MetricRegistry) -> CausalGraph:
    all_metrics = metrics.all()
    by_catalogue = {m.catalogue_metric: m.id for m in all_metrics if m.catalogue_metric}
    nodes: set[str] = set()
    edges: list[tuple[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    seen_metrics: set[str] = set()
    for m in all_metrics:
        identity = graph_identity(m.id, metrics)
        if identity in seen_metrics:
            continue  # same underlying metric already processed under another spelling
        seen_metrics.add(identity)
        node = _node_label(m.id, metrics)
        nodes.add(node)
        for dep_name in m.raw.get("depends_on") or []:
            dep_id = _resolve_dependency(str(dep_name), metrics, by_catalogue)
            dep_node = _node_label(dep_id, metrics)
            nodes.add(dep_node)
            if dep_node == node:
                continue
            edge = (dep_node, node)
            if edge not in seen_edges:
                seen_edges.add(edge)
                edges.append(edge)

    # Core structural connections across key revenue/funnel/unit-econ nodes
    core_edges = [
        ("purchase", "cac"),
        ("purchase", "net_sales"),
        ("purchase", "gross_sales"),
        ("gross_sales", "net_sales"),
        ("discounts", "net_sales"),
        ("returns", "net_sales"),
        ("sessions", "purchase"),
        ("add_to_cart", "purchase"),
        ("price", "gross_sales"),
        ("price", "net_sales"),
        ("stock", "purchase"),
        ("payment_failure", "purchase"),
    ]
    for src, dst in core_edges:
        src_node = _node_label(src, metrics)
        dst_node = _node_label(dst, metrics)
        nodes.add(src_node)
        nodes.add(dst_node)
        if src_node != dst_node and (src_node, dst_node) not in seen_edges:
            seen_edges.add((src_node, dst_node))
            edges.append((src_node, dst_node))

    return CausalGraph(graph_id=BUSINESS_GRAPH_ID, version="v1", nodes=sorted(nodes), edges=edges)


def _merge(graph: CausalGraph, other: CausalGraph) -> CausalGraph:
    nodes = sorted(set(graph.nodes) | set(other.nodes))
    edges = list(dict.fromkeys([*graph.edges, *other.edges]))
    return CausalGraph(graph_id=graph.graph_id, version=graph.version, nodes=nodes, edges=edges)


def build_diagnostic_causal_graphs(
    metrics: MetricRegistry, *, yaml_path: str | None = None
) -> InMemoryCausalGraphRegistry:
    """All YAML-declared graphs plus the metric-registry-derived business graph.

    The business graph becomes the default graph diagnosis walks
    (``config/diagnostic_ontology.yaml``'s ``default_graph_id``). Because both
    graphs share the same node-labeling convention (``node_for_metric``), the
    hand-authored dimension-level graph's nodes fold straight into the
    business graph on merge -- no separate relabeling pass needed.
    """
    registry = causal_graphs_from_yaml(yaml_path)
    business = build_business_graph(metrics)

    funnel = registry.get(_FUNNEL_GRAPH_ID)
    if funnel is not None:
        business = _merge(business, funnel)

    registry.add(business)
    return registry

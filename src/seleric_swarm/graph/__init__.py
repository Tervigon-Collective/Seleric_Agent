"""Metric graph (M4.2): identity edges from the catalogue plus configured causal edges."""

from seleric_swarm.graph.build import build_graph, causal_edges, identity_edges
from seleric_swarm.graph.model import Edge, MetricGraph, SkippedNode

__all__ = ["Edge", "MetricGraph", "SkippedNode", "build_graph", "causal_edges", "identity_edges"]

"""Metric graph contracts (M4.2): identity edges from the catalogue, causal edges from config.
Cross-domain extensions (AG-06): verified joins, time basis, spend attribution, population attribution.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Edge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str  # parent / component / cause
    target: str  # child / composite / effect
    kind: Literal["identity", "causal"]
    # identity: how the component enters the composite ("numerator", "denominator", "term", "depends_on")
    role: str = ""
    coefficient: float | None = None
    sign: Literal[1, -1, 0] = 0  # causal: expected direction, 0 = unknown
    lag_days: int = 0
    graph_id: str = ""
    # Cross-domain diagnostic extensions (AG-06)
    join_type: Literal["verified", "attributed", "derived"] = "derived"  # identity edge join basis
    time_basis: Literal["event_time", "cohort_time", "order_time", "report_time"] = "event_time"
    spend_attribution: Literal["direct", "allocated", "shared"] = "direct"
    population_coverage: Literal["attributed", "unmatched", "partial"] = "attributed"
    contribution_type: Literal["observed", "causal", "post_ad"] = "observed"
    profit_level: Literal["post_ad", "contribution_margin", "net_profit"] = "post_ad"


class SkippedNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node: str
    graph_id: str
    reason: str


class MetricGraph(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    catalogue_version: str = ""
    edges: tuple[Edge, ...] = ()
    skipped: tuple[SkippedNode, ...] = ()

    def parents(self, node: str, kind: str | None = None) -> tuple[Edge, ...]:
        return tuple(e for e in self.edges if e.target == node and (kind is None or e.kind == kind))

    def children(self, node: str, kind: str | None = None) -> tuple[Edge, ...]:
        return tuple(e for e in self.edges if e.source == node and (kind is None or e.kind == kind))

    def components(self, node: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(e.source for e in self.parents(node, "identity")))

    def causes(self, node: str) -> tuple[Edge, ...]:
        return self.parents(node, "causal")

    def ancestors(self, node: str, *, max_depth: int = 8) -> tuple[str, ...]:
        """Every node upstream of ``node`` (breadth first, cycle safe)."""
        seen: dict[str, None] = {}
        frontier = [node]
        for _ in range(max_depth):
            nxt = [e.source for n in frontier for e in self.parents(n) if e.source not in seen and e.source != node]
            if not nxt:
                break
            seen.update(dict.fromkeys(nxt))
            frontier = list(dict.fromkeys(nxt))
        return tuple(seen)

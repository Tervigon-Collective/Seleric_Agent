"""What to do with an edge kind when building the candidate subgraph (plan §5.7, built out in Phase 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from seleric_swarm.diagnosis.domain.graph import Edge, Graph
from seleric_swarm.diagnosis.domain.registry import Registry


@dataclass(frozen=True)
class PolicyDecision:
    action: str  # include | exclude | measurement_hypothesis
    reason_code: str
    check_value: str = ""


class EdgePolicy(Protocol):
    kind: ClassVar[str]  # the EdgeKind value this policy decides

    def decide(self, edge: Edge, graph: Graph) -> PolicyDecision: ...


EDGE_POLICIES: Registry[EdgePolicy] = Registry("edge policy")
register_edge_policy = EDGE_POLICIES.register

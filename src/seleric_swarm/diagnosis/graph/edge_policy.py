"""What to do with an edge kind when building the candidate subgraph (plan §5.7, built out in Phase 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from seleric_swarm.diagnosis.domain.graph import Edge, Graph
from seleric_swarm.diagnosis.domain.registry import Registry

ACCOUNT = "account"  # the source builds the target by arithmetic: part of the outcome, never a cause
CAUSE = "cause"  # the source may move the target: walk through it and offer it as a candidate
EXCLUDE = "exclude"  # the source describes the same quantity: not a cause
MEASUREMENT = "measurement_hypothesis"  # as exclude, and the two measurements are to be compared


@dataclass(frozen=True)
class PolicyDecision:
    action: str  # ACCOUNT | CAUSE | EXCLUDE | MEASUREMENT
    reason_code: str
    check_value: str = ""


class EdgePolicy(Protocol):
    kind: ClassVar[str]  # the EdgeKind value this policy decides

    def decide(self, edge: Edge, graph: Graph) -> PolicyDecision: ...


EDGE_POLICIES: Registry[EdgePolicy] = Registry("edge policy")
register_edge_policy = EDGE_POLICIES.register

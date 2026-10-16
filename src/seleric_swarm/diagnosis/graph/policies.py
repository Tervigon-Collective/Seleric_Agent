"""What each kind of reviewed edge means for the candidate subgraph (plan §5.4). One policy per edge kind, registered in
``EDGE_POLICIES``; the builder asks the policy and never looks at the kind itself, so a kind the graph gains is one new
class here and nothing else.

Importing this module registers nothing (the registry starts empty, see the registry test); ``register_edge_policies``
is the one explicit place that does."""

from __future__ import annotations

from typing import ClassVar

from seleric_swarm.diagnosis.domain.graph import Edge, EdgeKind, Graph
from seleric_swarm.diagnosis.domain.registry import Registry
from seleric_swarm.diagnosis.graph.edge_policy import (
    ACCOUNT,
    CAUSE,
    EDGE_POLICIES,
    EXCLUDE,
    MEASUREMENT,
    EdgePolicy,
    PolicyDecision,
)


class _Accounting:
    """The source builds the target by arithmetic (composition, ratio, declared input): a part of the outcome that the
    decomposition reports, not an upstream cause of it."""

    kind: ClassVar[str]

    def decide(self, edge: Edge, graph: Graph) -> PolicyDecision:
        return PolicyDecision(ACCOUNT, f"accounting_{edge.kind.value}")


class Composition(_Accounting):
    kind: ClassVar[str] = EdgeKind.COMPOSITION.value


class RatioComponent(_Accounting):
    kind: ClassVar[str] = EdgeKind.RATIO_COMPONENT.value


class DeclaredInput(_Accounting):
    kind: ClassVar[str] = EdgeKind.DECLARED_INPUT.value


class Mechanism:
    """A reviewed business prior: a move in the source drives the target."""

    kind: ClassVar[str] = EdgeKind.MECHANISM.value

    def decide(self, edge: Edge, graph: Graph) -> PolicyDecision:
        return PolicyDecision(CAUSE, "mechanism")


class Feedback:
    """A lever's lagged reaction to results. It is a cause of the target only after at least a day; at lag 0 the
    arrow would run both ways within a day, which the estimator cannot tell apart."""

    kind: ClassVar[str] = EdgeKind.FEEDBACK.value

    def decide(self, edge: Edge, graph: Graph) -> PolicyDecision:
        if edge.lag_days is None or edge.lag_days[0] < 1:
            return PolicyDecision(EXCLUDE, "feedback_without_lag", str(edge.lag_days))
        return PolicyDecision(CAUSE, "feedback")


class Twin:
    """The same quantity on another date basis or grain: the outcome booked another way."""

    kind: ClassVar[str] = EdgeKind.TWIN.value

    def decide(self, edge: Edge, graph: Graph) -> PolicyDecision:
        return PolicyDecision(EXCLUDE, "twin_of_outcome")


class CoMeasure:
    """The same events counted by another system. Not a cause, and a divergence between the two is itself a finding."""

    kind: ClassVar[str] = EdgeKind.CO_MEASURE.value

    def decide(self, edge: Edge, graph: Graph) -> PolicyDecision:
        return PolicyDecision(MEASUREMENT, "co_measure_of_outcome")


class Subset:
    """Part of the same events as the other end."""

    kind: ClassVar[str] = EdgeKind.SUBSET.value

    def decide(self, edge: Edge, graph: Graph) -> PolicyDecision:
        return PolicyDecision(EXCLUDE, "subset_of_outcome")


class Allocation:
    """A modelled share of the other end (a product's part of a total)."""

    kind: ClassVar[str] = EdgeKind.ALLOCATION.value

    def decide(self, edge: Edge, graph: Graph) -> PolicyDecision:
        return PolicyDecision(EXCLUDE, "allocation_of_outcome")


_SHIPPED: tuple[type[EdgePolicy], ...] = (
    Composition, RatioComponent, DeclaredInput, Mechanism, Feedback, Twin, CoMeasure, Subset, Allocation,
)


def register_edge_policies() -> Registry[EdgePolicy]:
    """Register the shipped policies (once) and return the registry. Safe to call repeatedly."""
    for policy in _SHIPPED:
        if policy.kind not in EDGE_POLICIES.kinds():
            EDGE_POLICIES.register(policy)
    return EDGE_POLICIES

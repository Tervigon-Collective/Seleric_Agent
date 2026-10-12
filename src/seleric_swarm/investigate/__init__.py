"""Investigation controller (M4.3): a deep "why" as a code state machine."""

from seleric_swarm.investigate.controller import Investigation
from seleric_swarm.investigate.state import (
    NON_ADDITIVE_AGGREGATIONS,
    Attributor,
    Candidate,
    Finding,
    InvestigatePolicy,
    InvestigateRequest,
    InvestigationState,
    MetricReader,
    Segment,
    Selector,
    aggregation_is_additive,
)

__all__ = [
    "Attributor",
    "Candidate",
    "Finding",
    "InvestigatePolicy",
    "InvestigateRequest",
    "Investigation",
    "InvestigationState",
    "MetricReader",
    "NON_ADDITIVE_AGGREGATIONS",
    "Segment",
    "Selector",
    "aggregation_is_additive",
]

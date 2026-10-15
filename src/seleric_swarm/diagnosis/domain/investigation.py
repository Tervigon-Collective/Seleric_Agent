"""What an operator receives and returns (plan §5.7). Provisional in detail, stable in shape: Phases 2-4 add fields
to ``Finding`` data and ledger actions; they do not change who calls whom."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.graph import Graph
from seleric_swarm.diagnosis.domain.types import Scope, Window


class EvidenceBasis(StrEnum):
    """How a finding is known (plan §5.7). ``PRIOR`` is the graph's say-so and is never counted as evidence."""

    IDENTITY = "identity"  # exact accounting
    STATISTICAL = "statistical"  # robust tests
    CAUSAL = "causal"  # graph + estimator + refuters
    PRIOR = "prior"


@dataclass(frozen=True)
class Focus:
    """Where an operator looks: a metric (graph node) in a scope and window, at a place in the search tree."""

    node: str
    scope: Scope
    window: Window
    depth: int = 0
    branch: int = 0
    parent_hypothesis: str | None = None


@dataclass(frozen=True)
class Cost:
    queries: int = 0
    ms: int = 0


@dataclass(frozen=True)
class Applicability:
    applicable: bool
    reason_code: str  # why not, or why yes; shown in the trace
    expected_cost: Cost = field(default_factory=Cost)


@dataclass(frozen=True)
class Finding:
    kind: str
    basis: EvidenceBasis
    subject: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class LedgerUpdate:
    hypothesis: str
    action: str  # open | test | resolve | supersede
    data: Mapping[str, Any]


@dataclass(frozen=True)
class EventDraft:
    """An event an operator wants emitted; the scheduler's emitter numbers and stamps it."""

    kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class OperatorResult:
    findings: tuple[Finding, ...] = ()
    proposed: tuple[LedgerUpdate, ...] = ()
    follow_ups: tuple[Focus, ...] = ()
    cost: Cost = field(default_factory=Cost)
    events: tuple[EventDraft, ...] = ()


@dataclass(frozen=True)
class InvestigationContext:
    outcome: str
    window: Window
    scope: Scope
    graph: Graph
    config: DiagnosisConfig
    seed: int

"""The candidate subgraph of one investigation, as frozen values (plan §5.4, P3.1).

It is what the reviewed graph says about *this* outcome: the metrics that build it by arithmetic (the accounting
tree), the metrics that can move it through reviewed mechanism edges (the candidate causes, each with the path it was
reached by), the metrics set aside with the reason, and the other measurements of the same quantity. The tool builds it
once, from the graph and the catalogue, and freezes it in the ``GatherPlan``, so a replay reports the subgraph the run
used and not the one today's graph file would give."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from seleric_swarm.diagnosis.domain.canonical import fingerprint
from seleric_swarm.diagnosis.domain.graph import EdgeKind

# Strength of a reviewed edge, weakest to strongest. A confidence the graph uses that is not listed ranks below all.
CONFIDENCE_ORDER = ("moderate", "strong", "structural")
# The peer kind whose divergence is a finding in itself (the same events counted by another system). A twin is the same
# quantity on another date basis or grain and legitimately differs day to day.
COMEASURE_KIND = EdgeKind.CO_MEASURE.value
# An edge the reviewers marked as a near-identity (the source is built into the target): the source is a part of what
# the target is, so it is walked through and not offered as a cause of it (D-51).
NEAR_IDENTITY = "structural"


def confidence_rank(confidence: str) -> int:
    return CONFIDENCE_ORDER.index(confidence) + 1 if confidence in CONFIDENCE_ORDER else 0


@dataclass(frozen=True)
class Candidate:
    """A metric that can move the outcome, reached from the accounting tree by ``distance`` reviewed edges.

    ``via`` is the node its first edge points into (one step nearer the outcome) and ``anchor`` the outcome or
    accounting-tree node the path ends in (``path`` lists the nodes from ``via`` to ``anchor``); ``kind`` and ``edge_confidence`` are the first edge's; ``path_confidence`` is the weakest edge on
    the path; ``lag_days`` is the path's total lag range and ``direction`` the sign a rise in the metric gives the
    anchor (``+``, ``-`` or ``+/-`` when it varies or is not stated)."""

    metric: str
    distance: int
    via: str
    anchor: str
    kind: str
    edge_confidence: str
    path_confidence: str
    direction: str
    lag_days: tuple[int, int]
    tier: int
    path: tuple[str, ...] = ()  # the metrics between this one and the anchor, nearest first, the anchor last

    @property
    def chain(self) -> tuple[tuple[str, str], ...]:
        """The edges from this metric to its anchor, in order: (metric, via), (via, next), ... (last, anchor)."""
        nodes = (self.metric, *self.path)
        return tuple(pairwise(nodes))

    @property
    def intermediates(self) -> tuple[str, ...]:
        """The metrics the effect must pass through to reach the anchor (the anchor itself is part of the outcome)."""
        return self.path[:-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric, "distance": self.distance, "via": self.via, "anchor": self.anchor,
            "kind": self.kind, "edge_confidence": self.edge_confidence, "path_confidence": self.path_confidence, "direction": self.direction,
            "lag_days": list(self.lag_days), "tier": self.tier, "path": list(self.path),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Candidate:
        lag = raw["lag_days"]
        return cls(
            metric=str(raw["metric"]), distance=int(raw["distance"]), via=str(raw["via"]), anchor=str(raw["anchor"]),
            kind=str(raw["kind"]), edge_confidence=str(raw.get("edge_confidence", raw["path_confidence"])),
            path_confidence=str(raw["path_confidence"]), direction=str(raw["direction"]),
            lag_days=(int(lag[0]), int(lag[1])), tier=int(raw["tier"]), path=tuple(str(m) for m in raw.get("path", ())),
        )


@dataclass(frozen=True)
class EdgeSpec:
    """A reviewed causal edge on some candidate's path, as the verification needs it."""

    source: str
    target: str
    sign: str  # +, - or +/-
    lag_days: tuple[int, int]
    confidence: str

    @property
    def structural(self) -> bool:
        return self.confidence == NEAR_IDENTITY

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "sign": self.sign, "lag_days": list(self.lag_days), "confidence": self.confidence}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> EdgeSpec:
        return cls(str(raw["source"]), str(raw["target"]), str(raw["sign"]), (int(raw["lag_days"][0]), int(raw["lag_days"][1])), str(raw["confidence"]))


@dataclass(frozen=True)
class Exclusion:
    """A metric the walk reached (or the outcome's own graph names) and set aside, with why.

    ``action`` is ``exclude`` (not offered as a cause) or ``measurement_hypothesis`` (not a cause either, but the
    two measurements of one quantity are to be compared: ``check_value`` names the other one)."""

    metric: str
    reason_code: str
    check_value: str
    action: str = "exclude"

    def to_dict(self) -> dict[str, str]:
        return {"metric": self.metric, "reason_code": self.reason_code, "check_value": self.check_value, "action": self.action}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Exclusion:
        return cls(str(raw["metric"]), str(raw["reason_code"]), str(raw["check_value"]), str(raw.get("action", "exclude")))


@dataclass(frozen=True)
class Peer:
    """Another measurement of the same quantity as ``metric`` (an accounting-tree node or the outcome): ``kind`` says
    how the graph relates them (twin, co_measure, subset, allocation), ``expected`` what the reviewers expect of the
    two (e.g. that they agree)."""

    metric: str
    peer: str
    kind: str
    expected: str

    def to_dict(self) -> dict[str, str]:
        return {"metric": self.metric, "peer": self.peer, "kind": self.kind, "expected": self.expected}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Peer:
        return cls(str(raw["metric"]), str(raw["peer"]), str(raw["kind"]), str(raw["expected"]))


@dataclass(frozen=True)
class Subgraph:
    outcome: str
    graph_version: str
    accounting: tuple[str, ...]  # the outcome's arithmetic parts, nearest first (the outcome itself is not in it)
    candidates: tuple[Candidate, ...]  # in discovery order (distance, then metric); ranking is a separate step
    exclusions: tuple[Exclusion, ...]
    peers: tuple[Peer, ...]
    edges_walked: int
    depth_limit: int
    edges: tuple[EdgeSpec, ...] = ()  # the causal edges on the candidates' paths

    @property
    def digest(self) -> str:
        return fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome, "graph_version": self.graph_version, "accounting": list(self.accounting),
            "candidates": [c.to_dict() for c in self.candidates], "exclusions": [e.to_dict() for e in self.exclusions],
            "peers": [p.to_dict() for p in self.peers], "edges_walked": self.edges_walked, "depth_limit": self.depth_limit,
            **({"edges": [e.to_dict() for e in self.edges]} if self.edges else {}),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Subgraph:
        return cls(
            outcome=str(raw["outcome"]), graph_version=str(raw["graph_version"]),
            accounting=tuple(str(m) for m in raw["accounting"]),
            candidates=tuple(Candidate.from_dict(c) for c in raw["candidates"]),
            exclusions=tuple(Exclusion.from_dict(e) for e in raw["exclusions"]),
            peers=tuple(Peer.from_dict(p) for p in raw["peers"]),
            edges_walked=int(raw["edges_walked"]), depth_limit=int(raw["depth_limit"]),
            edges=tuple(EdgeSpec.from_dict(e) for e in raw.get("edges") or ()),
        )

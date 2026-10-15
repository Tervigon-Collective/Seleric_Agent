"""The reviewed metric dependency graph as typed, immutable values (plan §5.4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class EdgeKind(StrEnum):
    COMPOSITION = "composition"  # target = sum of sign * source
    RATIO_COMPONENT = "ratio_component"  # target = numerator / denominator
    DECLARED_INPUT = "declared_input"  # target is computed from source, role not stated
    TWIN = "twin"  # same quantity on another date basis or grain; never both in one causal model
    CO_MEASURE = "co_measure"  # same events counted by another system; a divergence is a data observation
    SUBSET = "subset"  # source is part of the same events as target
    ALLOCATION = "allocation"  # target is a model share of source
    MECHANISM = "mechanism"  # business prior: a move in source drives target
    FEEDBACK = "feedback"  # lagged reaction of a lever to results


# Kinds that state how one number is built from others (exact, checkable on the data).
ACCOUNTING_KINDS = frozenset({EdgeKind.COMPOSITION, EdgeKind.RATIO_COMPONENT, EdgeKind.DECLARED_INPUT})
# Kinds that claim a causal direction.
CAUSAL_KINDS = frozenset({EdgeKind.MECHANISM, EdgeKind.FEEDBACK})
# Kinds that say two series measure the same thing: never causes of each other.
SAME_QUANTITY_KINDS = frozenset({EdgeKind.TWIN, EdgeKind.CO_MEASURE, EdgeKind.SUBSET, EdgeKind.ALLOCATION})


class Direction(StrEnum):
    """Sign of a causal prior: a rise in the source moves the target this way."""

    POSITIVE = "+"
    NEGATIVE = "-"
    EITHER = "+/-"


@dataclass(frozen=True)
class Tier:
    id: int
    name: str
    note: str


@dataclass(frozen=True)
class Hierarchy:
    name: str
    levels: tuple[str, ...]
    views: tuple[str, ...]


@dataclass(frozen=True)
class Ratio:
    numerator: str
    denominator: str


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    view: str
    category: str
    aggregation: str
    unit: str
    grain: str
    date_axis: str
    status: str
    tier: int
    allocated: bool
    formula: str
    ratio: Ratio | None
    same_quantity_group: tuple[str, ...]


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: EdgeKind
    basis: str
    confidence: str
    coefficient: int | None = None  # composition: +1 or -1
    direction: Direction | None = None  # mechanism / feedback
    lag_days: tuple[int, int] | None = None  # mechanism / feedback: inclusive range
    role: str | None = None  # ratio_component: numerator or denominator
    verified_by: str | None = None
    twin_of: tuple[str, ...] = ()
    systems: tuple[str, ...] = ()
    expected: str | None = None
    note: str | None = None
    rate: str | None = None
    key: str | None = None


@dataclass(frozen=True)
class GraphIssue:
    """Something the loader found wrong with one part of the graph. The rest of the graph stays usable."""

    code: str
    subject: str
    detail: str


@dataclass(frozen=True)
class Graph:
    """Nodes, typed edges, tiers and hierarchies, with the versions that identify this exact graph."""

    schema_version: int
    version: str  # digest of the graph document as loaded
    catalogue_version: str  # catalogue the nodes were checked against; empty when they were not checked
    nodes: Mapping[str, Node]
    edges: tuple[Edge, ...]
    tiers: Mapping[int, Tier]
    hierarchies: Mapping[str, Hierarchy]
    edge_kind_notes: Mapping[str, str]
    _into: Mapping[str, tuple[Edge, ...]] = field(init=False, repr=False, compare=False)
    _out: Mapping[str, tuple[Edge, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        into: dict[str, list[Edge]] = {}
        out: dict[str, list[Edge]] = {}
        for edge in self.edges:
            into.setdefault(edge.target, []).append(edge)
            out.setdefault(edge.source, []).append(edge)
        object.__setattr__(self, "_into", {k: tuple(v) for k, v in into.items()})
        object.__setattr__(self, "_out", {k: tuple(v) for k, v in out.items()})

    def node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

    def edges_into(self, node_id: str, kinds: frozenset[EdgeKind] | None = None) -> tuple[Edge, ...]:
        found = self._into.get(node_id, ())
        return found if kinds is None else tuple(e for e in found if e.kind in kinds)

    def edges_from(self, node_id: str, kinds: frozenset[EdgeKind] | None = None) -> tuple[Edge, ...]:
        found = self._out.get(node_id, ())
        return found if kinds is None else tuple(e for e in found if e.kind in kinds)


@dataclass(frozen=True)
class LoadedGraph:
    """A graph plus everything the loader had to set aside, in the order it was found."""

    graph: Graph
    issues: tuple[GraphIssue, ...]
    excluded_nodes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogueFacts:
    """What the live catalogue says about one metric, as far as the graph depends on it."""

    aggregation: str
    unit: str
    grain: str
    view: str

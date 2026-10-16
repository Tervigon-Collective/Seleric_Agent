"""The candidate subgraph of an outcome (plan §5.4, P3.1): a pure function of the reviewed graph, the outcome and which
metrics the data source can serve.

1. The **accounting tree**: every metric that builds the outcome by arithmetic, found by walking the edges whose policy
   says ``account`` (composition, ratio, declared input) into the outcome. These are reported by the decomposition,
   never offered as causes.
2. The **candidate causes**: from the outcome and its accounting tree, walk backwards over the edges whose policy says
   ``cause`` (mechanism, and feedback at a lag of a day or more) as far as ``max_depth`` edges. A metric reached by more
   than one path keeps the nearest, and among equals the strongest. The walk goes through a metric the data cannot serve
   or a policy sets aside, so what lies beyond it is still reached; such a metric is just not offered.
3. The **exclusions**: a reached metric that is the same quantity as the outcome or a part of it (twin, co-measure,
   subset, allocation), is computed from the outcome, or that ``usable`` rejects. Each carries a reason code.
4. The **peers**: the other measurements of the same quantity as the outcome and its parts, for the data checks that
   compare two measurements of one thing.

Which policy applies to an edge kind is the registry's business (``policies.py``); nothing here names a kind or a metric."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping

from seleric_swarm.diagnosis.domain.graph import SAME_QUANTITY_KINDS, Direction, Edge, Graph
from seleric_swarm.diagnosis.domain.subgraph import (
    Candidate,
    Exclusion,
    Peer,
    Subgraph,
    confidence_rank,
)
from seleric_swarm.diagnosis.graph.edge_policy import (
    ACCOUNT,
    CAUSE,
    EDGE_POLICIES,
    EXCLUDE,
    MEASUREMENT,
    EdgePolicy,
    PolicyDecision,
)
from seleric_swarm.diagnosis.graph.policies import register_edge_policies

DEFAULT_MAX_DEPTH = 8  # the reviewed mechanism edges are few; the walk is cheap, the ordering and the cap pick what is offered

Usable = Callable[[str], str | None]  # metric -> None when it can be offered, else the reason code it cannot


def _always_usable(metric: str) -> None:
    return None


def _combine(a: str, b: Direction | None) -> str:
    if b is None or a == Direction.EITHER or b == Direction.EITHER:
        return Direction.EITHER.value
    return Direction.POSITIVE.value if a == b else Direction.NEGATIVE.value


def _lag(edge: Edge) -> tuple[int, int]:
    return edge.lag_days if edge.lag_days is not None else (0, 0)


class _Walk:
    def __init__(self, graph: Graph, outcome: str, usable: Usable, max_depth: int) -> None:
        register_edge_policies()
        self.graph, self.outcome, self.usable, self.max_depth = graph, outcome, usable, max_depth
        self.policies: Mapping[str, EdgePolicy] = {k: EDGE_POLICIES.get(k)() for k in EDGE_POLICIES.kinds()}
        self.edges_walked = 0
        self.refused: list[Exclusion] = []  # causal-looking edges a policy turned down

    def decide(self, edge: Edge) -> PolicyDecision:
        return self.policies[edge.kind.value].decide(edge, self.graph)

    def accounting(self) -> list[str]:
        """Nearest first, then by id."""
        seen, order = {self.outcome}, []
        level = [self.outcome]
        while level:
            nxt: set[str] = set()
            for node in level:
                for edge in self.graph.edges_into(node):
                    self.edges_walked += 1
                    if self.decide(edge).action == ACCOUNT and edge.source not in seen:
                        seen.add(edge.source)
                        nxt.add(edge.source)
            order += sorted(nxt)
            level = sorted(nxt)
        return order

    def computed_from_outcome(self) -> set[str]:
        """Metrics the outcome is a part of, directly or through other parts: they follow it, they cannot cause it."""
        seen, queue = set(), deque([self.outcome])
        while queue:
            for edge in self.graph.edges_from(queue.popleft()):
                if self.decide(edge).action == ACCOUNT and edge.target not in seen:
                    seen.add(edge.target)
                    queue.append(edge.target)
        return seen

    def reach(self, protected: set[str]) -> dict[str, Candidate]:
        """Every metric the causal edges reach, with the best path to it (nearest, then strongest, then by id)."""
        reached: dict[str, Candidate] = {}
        level: list[tuple[str, str, tuple[int, int], str, str]] = [  # node, anchor, lag, direction, path confidence
            (p, p, (0, 0), Direction.POSITIVE.value, "structural") for p in sorted(protected)
        ]
        for distance in range(1, self.max_depth + 1):
            best: dict[str, tuple[tuple[int, str], Candidate, tuple[str, str, tuple[int, int], str, str]]] = {}
            for node, anchor, lag, direction, path_conf in level:
                for edge in self.graph.edges_into(node):
                    self.edges_walked += 1
                    source = edge.source
                    if source in protected or source in reached:
                        continue
                    decision = self.decide(edge)
                    if decision.action != CAUSE:
                        if decision.action == EXCLUDE and edge.kind not in SAME_QUANTITY_KINDS:
                            self.refused.append(Exclusion(source, decision.reason_code, decision.check_value))
                        continue
                    conf = edge.confidence if confidence_rank(edge.confidence) <= confidence_rank(path_conf) else path_conf
                    node_meta = self.graph.node(source)
                    cand = Candidate(
                        metric=source, distance=distance, via=node, anchor=anchor, kind=edge.kind.value,
                        path_confidence=conf, direction=_combine(direction, edge.direction),
                        lag_days=(lag[0] + _lag(edge)[0], lag[1] + _lag(edge)[1]),
                        tier=node_meta.tier if node_meta is not None else -1,
                    )
                    key = (-confidence_rank(conf), node)
                    if source not in best or key < best[source][0]:
                        best[source] = (key, cand, (source, anchor, cand.lag_days, cand.direction, conf))
            if not best:
                break
            for source in sorted(best):
                reached[source] = best[source][1]
            level = [best[source][2] for source in sorted(best)]
        return reached

    def same_quantity(self, node: str) -> list[tuple[Edge, str]]:
        """The edges that make ``node`` the same quantity as another metric, with the other end."""
        found = [(e, e.source) for e in self.graph.edges_into(node, SAME_QUANTITY_KINDS)]
        return found + [(e, e.target) for e in self.graph.edges_from(node, SAME_QUANTITY_KINDS)]


def build_subgraph(
    graph: Graph, outcome: str, *, usable: Usable = _always_usable, max_depth: int = DEFAULT_MAX_DEPTH
) -> Subgraph:
    walk = _Walk(graph, outcome, usable, max_depth)
    accounting = walk.accounting()
    protected = {outcome, *accounting}
    following = walk.computed_from_outcome()
    reached = walk.reach(protected)

    candidates: list[Candidate] = []
    exclusions: list[Exclusion] = []
    for metric, cand in sorted(reached.items(), key=lambda kv: (kv[1].distance, kv[0])):
        if metric in following:
            exclusions.append(Exclusion(metric, "computed_from_outcome", outcome))
            continue
        same = next(((e, other) for e, other in walk.same_quantity(metric) if other in protected), None)
        if same is not None:
            decision = walk.decide(same[0])
            exclusions.append(Exclusion(
                metric, decision.reason_code, same[1], "measurement_hypothesis" if decision.action == MEASUREMENT else "exclude",
            ))
            continue
        if (reason := usable(metric)) is not None:
            exclusions.append(Exclusion(metric, reason, ""))
            continue
        candidates.append(cand)

    peers: list[Peer] = []
    for node in sorted(protected):
        for edge, other in walk.same_quantity(node):
            if other not in protected:
                peers.append(Peer(node, other, edge.kind.value, edge.expected or ""))
    exclusions += walk.refused
    return Subgraph(
        outcome=outcome, graph_version=graph.version, accounting=tuple(accounting), candidates=tuple(candidates),
        exclusions=tuple(sorted(set(exclusions), key=lambda e: (e.metric, e.reason_code))),
        peers=tuple(sorted(set(peers), key=lambda p: (p.metric, p.peer, p.kind))),
        edges_walked=walk.edges_walked, depth_limit=max_depth,
    )

"""Where the dependency graph comes from."""

from __future__ import annotations

from typing import Protocol

from seleric_swarm.diagnosis.domain.graph import LoadedGraph


class GraphPort(Protocol):
    def load(self) -> LoadedGraph:
        """The graph, checked, with every problem found listed rather than raised.

        Raises ``GraphRejected`` only when no part of the graph can be trusted."""
        ...

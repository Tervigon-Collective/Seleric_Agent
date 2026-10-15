"""The dependency graph vendored in this repository (``causal/data/dependency_graph.json``).

The graph is generated and reviewed in Agent Core and copied here (see ``PROVENANCE.md`` next to the file);
it is never edited in this repository."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from seleric_swarm.diagnosis.domain.graph import CatalogueFacts, LoadedGraph
from seleric_swarm.diagnosis.graph.loader import load_graph

VENDORED_GRAPH = Path(__file__).resolve().parents[2] / "causal" / "data" / "dependency_graph.json"


def facts_from_definitions(definitions: Mapping[str, Mapping[str, Any]]) -> dict[str, CatalogueFacts]:
    """Catalogue definitions (the ``catalogue_get_metrics`` shape: live, or stored in a fixture) -> the facts the
    graph is checked against. A definition missing any of the four facts gets no entry, so the loader reports the
    metric as absent rather than comparing against a guess."""
    out: dict[str, CatalogueFacts] = {}
    for metric_id, definition in definitions.items():
        mapping = definition.get("cube_mapping") or {}
        values = (definition.get("aggregation"), definition.get("unit"), definition.get("grain"), mapping.get("view"))
        if all(isinstance(v, str) and v for v in values):
            out[metric_id] = CatalogueFacts(*values)  # type: ignore[arg-type]
    return out


class VendoredGraphAdapter:
    """``GraphPort`` over a graph file. Pass ``live`` (the catalogue's current facts) to check every metric
    against the catalogue the data will be read from; without it the graph is validated but not cross-checked."""

    def __init__(
        self,
        path: Path = VENDORED_GRAPH,
        *,
        live: Mapping[str, CatalogueFacts] | None = None,
        catalogue_version: str = "",
    ) -> None:
        self._path = path
        self._live = live
        self._catalogue_version = catalogue_version

    def load(self) -> LoadedGraph:
        document = json.loads(self._path.read_text(encoding="utf-8"))
        return load_graph(document, live=self._live, catalogue_version=self._catalogue_version)

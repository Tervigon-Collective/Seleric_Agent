"""Known data incidents (injected incidents file) as events and as data health."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from seleric_swarm.context.model import ContextEvent, ContextWindow, DataHealth
from seleric_swarm.forecasting.quality import Incident, IncidentsFile
from seleric_swarm.semantic import SemanticReadModel


class IncidentProvider:
    id = "incidents"

    def __init__(self, incidents: IncidentsFile) -> None:
        self._incidents = incidents

    def _overlapping(
        self, window: ContextWindow, read_model: SemanticReadModel
    ) -> Iterator[tuple[Incident, date, date, tuple[str, ...]]]:
        known = read_model.metric_ids()
        for inc in self._incidents.incidents:
            start, end = date.fromisoformat(inc.start), date.fromisoformat(inc.end)
            if not window.overlaps(start, end):
                continue
            # Incidents name catalogue ids; ids the catalogue no longer has are dropped.
            metrics = tuple(m for m in inc.metrics if m in known)
            if inc.metrics and not metrics:
                continue
            yield inc, start, end, metrics

    def events(
        self, window: ContextWindow, scope: dict[str, str], read_model: SemanticReadModel
    ) -> list[ContextEvent]:
        del scope
        return [
            ContextEvent(kind="data_incident", start=s, end=e, source=self.id, title=inc.id,
                         detail=inc.reason, metric_ids=m)
            for inc, s, e, m in self._overlapping(window, read_model)
        ]

    def health(
        self, window: ContextWindow, metric_ids: tuple[str, ...], read_model: SemanticReadModel
    ) -> list[DataHealth]:
        out: list[DataHealth] = []
        for inc, s, e, m in self._overlapping(window, read_model):
            hit = [x for x in metric_ids if not m or x in m]
            out += [DataHealth(metric_id=x, day=max(s, window.start), status="incident",
                               detail=f"{inc.id} {s}..{e}: {inc.reason}".strip()) for x in hit]
        return out

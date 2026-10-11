"""Change events the catalogue itself declares (launches, price changes, campaign starts...).

A metric is a change-event source when the read model marks it: ``extra.context_event`` is
true (or names the event kind) and ``extra.event_label_dimension`` names the dimension whose
values describe each change. Nothing is named in code; with no such metric, no events.
"""

from __future__ import annotations

from typing import Protocol

from seleric_swarm.context.model import ContextEvent, ContextWindow
from seleric_swarm.contracts.pipeline import FilterSpec, WindowSpec
from seleric_swarm.semantic import SemanticReadModel

_DEFAULT_KIND = "change"


class EventReader(Protocol):
    """Read port (the pipeline's ``PlanReader`` satisfies it)."""

    async def breakdown(
        self, metric_id: str, window: WindowSpec, dimension: str, filters: tuple[FilterSpec, ...]
    ) -> dict[str, float]: ...


def change_sources(read_model: SemanticReadModel) -> list[tuple[str, str, str]]:
    """``(metric_id, label_dimension, kind)`` for every catalogue-declared change source."""
    out: list[tuple[str, str, str]] = []
    for m in read_model.metrics:
        flag = m.extra.get("context_event")
        label_dim = m.extra.get("event_label_dimension")
        if flag and isinstance(label_dim, str) and label_dim in m.supported_dimensions:
            out.append((m.id, label_dim, flag if isinstance(flag, str) else _DEFAULT_KIND))
    return out


class ChangeLogProvider:
    """Async provider: queries each declared change source over the window."""

    id = "changes"

    def __init__(self, reader: EventReader) -> None:
        self._reader = reader

    async def aevents(
        self, window: ContextWindow, scope: dict[str, str], read_model: SemanticReadModel
    ) -> list[ContextEvent]:
        filters = tuple(FilterSpec(dimension=k, operator="in", values=(v,)) for k, v in scope.items()
                        if read_model.dimension_ids() and k in read_model.dimension_ids())
        spec = WindowSpec(role="context", start=window.start, end=window.end)
        out: list[ContextEvent] = []
        for metric_id, label_dim, kind in change_sources(read_model):
            carried = tuple(f for f in filters if read_model.carries(metric_id, f.dimension))
            values = await self._reader.breakdown(metric_id, spec, label_dim, carried)
            for label, count in sorted(values.items()):
                if count:
                    out.append(ContextEvent(kind=kind, start=window.start, end=window.end, source=self.id,
                                            title=label, detail=f"{metric_id}={count:g}",
                                            scope={k: v for k, v in scope.items()}))
        return out

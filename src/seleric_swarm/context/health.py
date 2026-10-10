"""Data health from view freshness in the read model (when Core reports it)."""

from __future__ import annotations

from seleric_swarm.context.model import ContextWindow, DataHealth
from seleric_swarm.semantic import SemanticReadModel

# Machine freshness statuses that mean the view is current; anything else is reported as late.
_CURRENT = frozenset({"fresh", "ok", "current", "up_to_date"})


class FreshnessHealth:
    id = "freshness"

    def health(
        self, window: ContextWindow, metric_ids: tuple[str, ...], read_model: SemanticReadModel
    ) -> list[DataHealth]:
        del window
        views = {v.id: v.freshness for v in read_model.views if v.freshness}
        by_id = {m.id: m for m in read_model.metrics}
        out: list[DataHealth] = []
        for metric_id in metric_ids:
            record = by_id.get(metric_id)
            if record is None:
                continue
            status = views.get(record.view)
            if status and status.strip().lower() not in _CURRENT:
                out.append(DataHealth(metric_id=metric_id, status="late", detail=f"{record.view}: {status}"))
        return out

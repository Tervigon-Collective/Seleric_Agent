"""Data health from view freshness in the read model (when Core reports it)."""

from __future__ import annotations

from seleric_swarm.context.model import ContextWindow, DataHealth, DataHealthStatus
from seleric_swarm.semantic import SemanticReadModel

# Machine freshness statuses mapped to the health vocabulary. Current views are
# silent; anything unrecognised is "unknown" (not assumed late) so an unsupported
# status is visible rather than silently trusted or silently downgraded (AG-01).
_CURRENT = frozenset({"fresh", "ok", "current", "up_to_date"})
_STATUS_MAP: dict[str, DataHealthStatus] = {
    "late": "late",
    "delayed": "late",
    "stale": "late",
    "incomplete": "incomplete",
    "partial": "incomplete",
    "incident": "incident",
    "provisional": "provisional",
    "preliminary": "provisional",
    "estimated": "provisional",
    "unknown": "unknown",
    "unavailable": "unknown",
    "error": "unknown",
}


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
            raw = views.get(record.view)
            if not raw:
                continue
            token = raw.strip().lower()
            if token in _CURRENT:
                continue
            mapped = _STATUS_MAP.get(token, "unknown")
            out.append(DataHealth(metric_id=metric_id, status=mapped, detail=f"{record.view}: {raw}"))
        return out

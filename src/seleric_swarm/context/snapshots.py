"""Data health from domain-health snapshots (injected as plain dicts; the pipeline loads them)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from seleric_swarm.context.model import ContextWindow, DataHealth
from seleric_swarm.semantic import SemanticReadModel

# Machine values of the snapshot contract (services/domain_health/models.py).
_STALE = frozenset({"STALE", "LATE"})
_MISSING = frozenset({"MISSING_DATA", "MCP_ERROR", "PARTIAL"})


class SnapshotHealth:
    id = "domain_health"

    def __init__(self, snapshots: Sequence[Mapping[str, Any]]) -> None:
        self._snapshots = snapshots

    def health(
        self, window: ContextWindow, metric_ids: tuple[str, ...], read_model: SemanticReadModel
    ) -> list[DataHealth]:
        del read_model
        wanted = set(metric_ids)
        out: dict[str, DataHealth] = {}
        for snap in self._snapshots:
            day = _day(snap.get("as_of"))
            if day is not None and not window.start <= day <= window.end:
                continue
            for m in snap.get("metrics") or []:
                mid = m.get("metric_id") if isinstance(m, Mapping) else None
                if mid not in wanted or mid in out:
                    continue
                flags = {str(f).upper() for f in m.get("quality_flags") or []}
                fresh = str(m.get("freshness") or "").upper()
                if flags & _MISSING:
                    out[mid] = DataHealth(metric_id=mid, day=day, status="incomplete", detail=",".join(sorted(flags)))
                elif fresh in _STALE:
                    out[mid] = DataHealth(metric_id=mid, day=day, status="late", detail=f"freshness={fresh}")
        return list(out.values())


def _day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None

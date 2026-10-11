"""Change-log provider (catalogue-declared, data-read) and domain-health snapshots."""

from __future__ import annotations

from datetime import date

from seleric_swarm.context import (
    ChangeLogProvider,
    ContextWindow,
    SnapshotHealth,
    agather_events,
    change_sources,
)
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "metrics": [
        {"id": "zz_launches", "supported_dimensions": ["zz_item", "zz_zone"], "context_event": "launch",
         "event_label_dimension": "zz_item"},
        {"id": "zz_take", "supported_dimensions": ["zz_zone"]},
        {"id": "zz_bad", "context_event": True, "event_label_dimension": "zz_item"},  # does not carry it
    ],
    "dimensions": [{"id": "zz_item"}, {"id": "zz_zone"}],
})
WIN = ContextWindow(start=date(2026, 10, 1), end=date(2026, 10, 7))


class Reader:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def breakdown(self, metric_id, window, dimension, filters):
        self.calls.append((metric_id, dimension, filters))
        return {"new-a": 1.0, "none": 0.0}


def test_only_declared_and_carried_sources() -> None:
    assert change_sources(MODEL) == [("zz_launches", "zz_item", "launch")]


async def test_changes_are_read_and_scoped() -> None:
    reader = Reader()
    events = await agather_events([ChangeLogProvider(reader)], WIN, {"zz_zone": "north"}, MODEL)
    assert [(e.kind, e.title) for e in events] == [("launch", "new-a")]
    (_, _, filters), = reader.calls
    assert filters[0].dimension == "zz_zone" and filters[0].values == ("north",)


def test_snapshot_health() -> None:
    snaps = [{"as_of": "2026-10-05", "metrics": [
        {"metric_id": "zz_take", "freshness": "STALE"},
        {"metric_id": "zz_launches", "quality_flags": ["MISSING_DATA"]},
    ]}, {"as_of": "2026-09-01", "metrics": [{"metric_id": "zz_take", "quality_flags": ["MISSING_DATA"]}]}]
    health = {h.metric_id: h.status for h in SnapshotHealth(snaps).health(WIN, ("zz_take", "zz_launches"), MODEL)}
    assert health == {"zz_take": "late", "zz_launches": "incomplete"}

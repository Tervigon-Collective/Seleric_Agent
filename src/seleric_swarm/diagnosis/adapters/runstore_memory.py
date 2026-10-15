"""In-process run store (tests, scratch runs)."""

from __future__ import annotations

from seleric_swarm.diagnosis.domain.record import RunRecord
from seleric_swarm.diagnosis.ports.runstore import RunRecordNotFound


class MemoryRunStore:
    def __init__(self) -> None:
        self._records: dict[str, str] = {}

    def save(self, record: RunRecord) -> None:
        if record.investigation_id in self._records:
            raise ValueError(f"run record {record.investigation_id} already exists; records are written once")
        self._records[record.investigation_id] = record.to_json()

    def load(self, investigation_id: str) -> RunRecord:
        try:
            return RunRecord.from_json(self._records[investigation_id])
        except KeyError as exc:
            raise RunRecordNotFound(investigation_id) from exc

"""Run records as JSON files in a directory: what the replay CLI reads, and what a record exported for a bug report is."""

from __future__ import annotations

from pathlib import Path

from seleric_swarm.diagnosis.domain.record import RunRecord
from seleric_swarm.diagnosis.ports.runstore import RunRecordNotFound


class FileRunStore:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def _path(self, investigation_id: str) -> Path:
        if not investigation_id or "/" in investigation_id or "\\" in investigation_id or investigation_id.startswith("."):
            raise ValueError(f"not a usable investigation id: {investigation_id!r}")
        return self._dir / f"{investigation_id}.json"

    def save(self, record: RunRecord) -> None:
        path = self._path(record.investigation_id)
        self._dir.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:  # "x": a record is written once
            handle.write(record.to_json())

    def load(self, investigation_id: str) -> RunRecord:
        path = self._path(investigation_id)
        if not path.exists():
            raise RunRecordNotFound(investigation_id)
        return RunRecord.from_json(path.read_text(encoding="utf-8"))

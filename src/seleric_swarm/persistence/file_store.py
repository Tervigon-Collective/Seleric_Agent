"""Local-disk persistence for chat, missions, and V3 stores.

Postgres is the production backend. This exists so a local API without Docker
can keep threads and answers across reloads (``PERSISTENCE_BACKEND=file``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from seleric_swarm.contracts.lookup import MissionResult
from seleric_swarm.conversations.contracts import Artifact
from seleric_swarm.persistence.memory import InMemoryMissionStore
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.state.missions import InMemoryMissionStore as V3MissionStore
from seleric_swarm.state.missions import Mission

_STATE_VERSION = 1


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def dump_v3_mission(mission: Mission) -> dict[str, Any]:
    data = asdict(mission)
    for key in ("as_of", "created_at", "updated_at"):
        data[key] = _iso(data.get(key))
    return data


def load_v3_mission(raw: dict[str, Any]) -> Mission:
    payload = dict(raw)
    for key in ("as_of", "created_at", "updated_at"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            payload[key] = datetime.fromisoformat(value)
    return Mission(**payload)


class FileMissionStore(InMemoryMissionStore):
    def __init__(self, path: Path, *, on_change: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._path = path
        self._on_change = on_change
        self._loading = False
        self._restore(_read_json(path))

    def _restore(self, payload: dict[str, Any]) -> None:
        self._loading = True
        try:
            results = payload.get("results") or {}
            self._results = {
                mission_id: MissionResult.model_validate(row)
                for mission_id, row in results.items()
                if isinstance(row, dict)
            }
            raw = payload.get("raw") or {}
            self._raw = {key: value for key, value in raw.items() if isinstance(value, dict)}
            events = payload.get("events") or {}
            self._events = {
                key: [event for event in rows if isinstance(event, dict)]
                for key, rows in events.items()
                if isinstance(rows, list)
            }
        finally:
            self._loading = False

    def _persist(self) -> None:
        if self._loading:
            return
        _atomic_write(
            self._path,
            {
                "version": _STATE_VERSION,
                "results": {
                    mission_id: result.model_dump(mode="json")
                    for mission_id, result in self._results.items()
                },
                "raw": dict(self._raw),
                "events": dict(self._events),
            },
        )
        if self._on_change is not None:
            self._on_change()

    def put(self, result: MissionResult, raw_state: dict[str, Any] | None = None) -> None:
        super().put(result, raw_state)
        self._persist()


class FileV3MissionStore(V3MissionStore):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._loading = False
        self._restore(_read_json(path))

    def _restore(self, payload: dict[str, Any]) -> None:
        self._loading = True
        try:
            rows = payload.get("missions") or []
            self._by_id = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mission = load_v3_mission(row)
                self._by_id[mission.mission_id] = mission
        finally:
            self._loading = False

    def _persist(self) -> None:
        if self._loading:
            return
        _atomic_write(
            self._path,
            {
                "version": _STATE_VERSION,
                "missions": [dump_v3_mission(mission) for mission in self._by_id.values()],
            },
        )

    def create(self, mission: Mission) -> Mission:
        stored = super().create(mission)
        self._persist()
        return stored

    def update_status(self, mission_id: str, status: Any) -> Mission | None:
        stored = super().update_status(mission_id, status)
        self._persist()
        return stored

    def finish(
        self,
        mission_id: str,
        *,
        status: Any,
        final_response: str | None = None,
        error_code: str | None = None,
    ) -> Mission | None:
        stored = super().finish(
            mission_id,
            status=status,
            final_response=final_response,
            error_code=error_code,
        )
        self._persist()
        return stored


class FileV3ArtifactStore(InMemoryArtifactStore):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._loading = False
        self._restore(_read_json(path))

    def _restore(self, payload: dict[str, Any]) -> None:
        self._loading = True
        try:
            rows = payload.get("artifacts") or []
            self._by_id = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                artifact = Artifact.model_validate(row)
                self._by_id[artifact.id] = artifact
        finally:
            self._loading = False

    def _persist(self) -> None:
        if self._loading:
            return
        _atomic_write(
            self._path,
            {
                "version": _STATE_VERSION,
                "artifacts": [artifact.model_dump(mode="json") for artifact in self._by_id.values()],
            },
        )

    def put(self, artifact: Artifact) -> Artifact:
        stored = super().put(artifact)
        self._persist()
        return stored


@dataclass(frozen=True)
class FilePaths:
    root: Path

    @property
    def conversations(self) -> Path:
        return self.root / "conversations.json"

    @property
    def missions(self) -> Path:
        return self.root / "missions.json"

    @property
    def v3_missions(self) -> Path:
        return self.root / "v3_missions.json"

    @property
    def v3_artifacts(self) -> Path:
        return self.root / "v3_artifacts.json"


def file_paths(root: str | Path) -> FilePaths:
    return FilePaths(Path(root))

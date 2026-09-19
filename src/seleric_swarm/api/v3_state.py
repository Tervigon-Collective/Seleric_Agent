"""Process-level V3 mission/artifact stores for ``api/missions.py``.

``api/missions.py``'s stub endpoint previously built a throwaway
``InMemoryArtifactStore()`` per request and never persisted the ``Mission``
anywhere — the mission was un-retrievable the instant the response was sent,
which also meant the Office UI (``api/office/gateway.py``) had no raw state
to read for a V3 mission id. One shared pair of stores per process, mirrors
how ``main.py::get_runtime()`` holds one shared swarm_v2 ``MissionStore``.
"""

from __future__ import annotations

from pathlib import Path

from seleric_swarm.persistence.file_store import FileV3ArtifactStore, FileV3MissionStore, file_paths
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.state.missions import InMemoryMissionStore

_mission_store: InMemoryMissionStore | None = None
_artifact_store: InMemoryArtifactStore | None = None
_persist_dir: Path | None = None


def configure_v3_persistence(persist_path: str | Path | None) -> None:
    """Use local-disk V3 stores when ``PERSISTENCE_BACKEND=file``."""
    global _persist_dir, _mission_store, _artifact_store
    _persist_dir = Path(persist_path) if persist_path else None
    _mission_store = None
    _artifact_store = None


def get_v3_mission_store() -> InMemoryMissionStore:
    global _mission_store
    if _mission_store is None:
        if _persist_dir is not None:
            _mission_store = FileV3MissionStore(file_paths(_persist_dir).v3_missions)
        else:
            _mission_store = InMemoryMissionStore()
    return _mission_store


def get_v3_artifact_store() -> InMemoryArtifactStore:
    global _artifact_store
    if _artifact_store is None:
        if _persist_dir is not None:
            _artifact_store = FileV3ArtifactStore(file_paths(_persist_dir).v3_artifacts)
        else:
            _artifact_store = InMemoryArtifactStore()
    return _artifact_store


def reset_v3_stores() -> None:
    """Test helper — drop the process-level V3 stores."""
    global _mission_store, _artifact_store, _persist_dir
    _persist_dir = None
    _mission_store = InMemoryMissionStore()
    _artifact_store = InMemoryArtifactStore()

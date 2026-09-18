"""Process-level V3 mission/artifact stores for ``api/missions.py``.

``api/missions.py``'s stub endpoint previously built a throwaway
``InMemoryArtifactStore()`` per request and never persisted the ``Mission``
anywhere — the mission was un-retrievable the instant the response was sent,
which also meant the Office UI (``api/office/gateway.py``) had no raw state
to read for a V3 mission id. One shared pair of stores per process, mirrors
how ``main.py::get_runtime()`` holds one shared swarm_v2 ``MissionStore``.
"""

from __future__ import annotations

from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.state.missions import InMemoryMissionStore

_mission_store: InMemoryMissionStore | None = None
_artifact_store: InMemoryArtifactStore | None = None


def get_v3_mission_store() -> InMemoryMissionStore:
    global _mission_store
    if _mission_store is None:
        _mission_store = InMemoryMissionStore()
    return _mission_store


def get_v3_artifact_store() -> InMemoryArtifactStore:
    global _artifact_store
    if _artifact_store is None:
        _artifact_store = InMemoryArtifactStore()
    return _artifact_store

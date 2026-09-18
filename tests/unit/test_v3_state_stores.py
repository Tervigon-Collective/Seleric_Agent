from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.state.missions import InMemoryMissionStore, Mission


def _evidence_artifact(mission_id: str) -> Artifact:
    payload = EvidenceArtifact(
        metric_id="metric.net_sales",
        grain="none",
        as_of=datetime.now(UTC),
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC),
        value=123.0,
        source_query={"metric_id": "metric.net_sales"},
    )
    return Artifact(
        workspace_id="ws-1",
        artifact_type="evidence",
        payload=payload.model_dump(mode="json"),
        classification="factual",
        evidence_ids=["ev-source"],
        provenance=ArtifactProvenance(evidence_ids=["ev-source"]),
        mission_id=mission_id,
    )


def test_artifact_store_put_get_and_immutability() -> None:
    store = InMemoryArtifactStore()
    artifact = _evidence_artifact("MS3-1")
    stored = store.put(artifact)
    assert store.get(stored.id) == stored
    assert store.list_for_mission("MS3-1") == [stored]
    with pytest.raises(ValueError, match="immutable"):
        store.put(stored)


def test_mission_store_create_and_update_status() -> None:
    store = InMemoryMissionStore()
    mission = Mission(
        mission_id="MS3-1",
        query="What were net sales yesterday?",
        as_of=datetime.now(UTC),
        workspace_id="ws-1",
        owner_user_id="user-1",
    )
    store.create(mission)
    assert store.get("MS3-1") is mission
    updated = store.update_status("MS3-1", "completed")
    assert updated is not None
    assert updated.status == "completed"
    assert store.list_for_workspace("ws-1") == [mission]

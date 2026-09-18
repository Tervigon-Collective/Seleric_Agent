"""V3 mission visibility in the Office UI's read path — the connectivity
gap found and fixed 2026-09-18 (docs/refactor/TASK_SHEET.md log)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from seleric_swarm.api.office import registry
from seleric_swarm.api.v3_state import get_v3_artifact_store, get_v3_mission_store
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.state.missions import Mission


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    from seleric_swarm.api.office import gateway

    class _EmptyStore:
        def get_raw(self, mission_id):
            return None

        def get(self, mission_id):
            return None

    class _Runtime:
        store = _EmptyStore()

    monkeypatch.setattr(gateway, "_runtime", lambda: _Runtime())
    registry.clear()

    from seleric_swarm import main

    return TestClient(main.app)


def _seed_v3_mission(mission_id: str, *, status: str = "completed") -> None:
    mission_store = get_v3_mission_store()
    mission_store.create(
        Mission(
            mission_id=mission_id,
            query="what were net sales yesterday?",
            as_of=datetime.now(UTC),
            workspace_id="default",
            owner_user_id="default",
            thread_id="thread-1",
            run_id="run-1",
        )
    )
    mission_store.finish(mission_id, status=status, final_response="net sales: 123", error_code=None)
    registry.register_mission(mission_id)
    artifact_store = get_v3_artifact_store()
    artifact_store.put(
        Artifact(
            workspace_id="default",
            artifact_type="evidence",
            payload={"metric_id": "metric.net_sales"},
            classification="factual",
            evidence_ids=["ev-source"],
            provenance=ArtifactProvenance(evidence_ids=["ev-source"]),
            mission_id=mission_id,
        )
    )


def test_v3_mission_appears_in_office_list(client: TestClient) -> None:
    _seed_v3_mission("MS3-office-1")
    body = client.get("/v1/office/missions").json()
    ids = [m["missionId"] for m in body["missions"]]
    assert "MS3-office-1" in ids


def test_v3_mission_snapshot_renders(client: TestClient) -> None:
    _seed_v3_mission("MS3-office-2")
    body = client.get("/v1/office/missions/MS3-office-2/snapshot").json()
    assert body["missionId"] == "MS3-office-2"
    assert body["status"] == "completed"
    assert body["stage"] == "complete"
    assert body["finalResponse"] == "net sales: 123"
    assert body["artifacts"]["evidence"] == 1
    assert body["trace"] == {"requestId": "run-1", "sessionId": "thread-1"}
    coordinator = next(a for a in body["agents"] if a["agentId"] == "coordinator")
    assert coordinator["missionLead"] is True


def test_unknown_mission_id_still_404s(client: TestClient) -> None:
    resp = client.get("/v1/office/missions/does-not-exist/snapshot")
    assert resp.status_code == 404

"""Office gateway HTTP surface — snapshot + SSE stream against the real router."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from seleric_swarm.api.office import registry
from seleric_swarm.contracts.lookup import MissionResult, TraceInfo
from seleric_swarm.persistence.memory import InMemoryMissionStore

RAW = {
    "route": "swarm",
    "mission_id": "MS-gw-1",
    "status": "running",
    "query": "Why has CAC increased?",
    "mission_lead": "funnel",
    "artifacts": {"evidence": [{"id": "E1"}], "anomaly": [{"id": "A1"}]},
    "events": [
        {"kind": "mission_created", "seq": 1, "ts": "2026-09-09T14:00:00Z", "family": "mission"},
        {"kind": "decomposition_created", "seq": 2, "ts": "2026-09-09T14:00:01Z", "family": "decomposition"},
        {"kind": "task_wave_executed", "seq": 3, "ts": "2026-09-09T14:00:02Z", "family": "task", "mission_lead": "performance"},
        {
            "kind": "leadership_transfer",
            "seq": 4,
            "ts": "2026-09-09T14:00:03Z",
            "family": "leadership",
            "from_agent": "performance_agent",
            "to_agent": "funnel_agent",
            "reason": "media stable",
        },
    ],
}


class _Runtime:
    def __init__(self) -> None:
        self.store = InMemoryMissionStore()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    from seleric_swarm.api.office import gateway

    rt = _Runtime()
    rt.store.put(
        MissionResult(
            mission_id="MS-gw-1",
            status="running",
            limitations=[],
            final_response=None,
            trace=TraceInfo(request_id="r", session_id="s"),
        ),
        RAW,
    )
    monkeypatch.setattr(gateway, "_runtime", lambda: rt)
    registry.clear()
    registry.register_mission("MS-gw-1")

    from seleric_swarm import main

    return TestClient(main.app)


def test_missions_list_includes_registered(client: TestClient) -> None:
    j = client.get("/v1/office/missions").json()
    assert any(m["missionId"] == "MS-gw-1" for m in j["missions"])


def test_missions_list_prefers_store_list_missions(client: TestClient) -> None:
    """When the store can enumerate, the gateway uses it (durable / multi-worker)."""
    from seleric_swarm.api.office import gateway, registry

    registry.clear()  # nothing in the in-process registry
    j = client.get("/v1/office/missions").json()
    row = next(m for m in j["missions"] if m["missionId"] == "MS-gw-1")
    assert row["query"] == "Why has CAC increased?"
    assert row["missionLead"] == "funnel"
    assert callable(getattr(gateway._runtime().store, "list_missions", None))


def test_snapshot_endpoint_derives_office_state(client: TestClient) -> None:
    j = client.get("/v1/office/missions/MS-gw-1/snapshot").json()
    assert j["missionId"] == "MS-gw-1"
    assert j["leadAgentId"] == "funnel_agent"
    assert len(j["agents"]) == 14
    assert j["timeline"][0]["eventType"] == "mission_started"
    assert j["artifacts"]["anomaly"] == 1


def test_snapshot_404_for_unknown(client: TestClient) -> None:
    assert client.get("/v1/office/missions/NOPE/snapshot").status_code == 404


def test_stream_emits_snapshot_then_done(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal mission streams exactly one snapshot frame then a done frame."""
    from seleric_swarm.api.office import gateway

    monkeypatch.setattr(gateway, "_POLL_INTERVAL_S", 0.05)
    rt = gateway._runtime()
    terminal = {**RAW, "status": "completed"}
    rt.store.put(rt.store.get("MS-gw-1"), terminal)

    with client.stream("GET", "/v1/office/missions/MS-gw-1/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())

    frames = [f for f in body.split("\n\n") if f.strip()]
    assert frames[0].startswith("event: snapshot")
    assert frames[-1].startswith("event: done")
    snap = json.loads(frames[0].split("data: ", 1)[1])
    assert snap["missionId"] == "MS-gw-1"
    assert snap["leadAgentId"] == "funnel_agent"

"""Mission persistence + events API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

import seleric_swarm.main as main_mod
from seleric_swarm.contracts.lookup import MissionResult, TraceInfo
from seleric_swarm.main import app
from seleric_swarm.persistence.memory import InMemoryMissionStore, extract_events, filter_events


def test_extract_and_filter_events():
    raw = {
        "events": [
            {"kind": "mission_created", "seq": 1, "family": "mission"},
            {"kind": "task_wave_executed", "seq": 2, "family": "task"},
            {"kind": "skeptic_pass", "seq": 3, "family": "skeptic"},
        ]
    }
    events = extract_events(raw)
    assert len(events) == 3
    only_task = filter_events(events, family="task")
    assert len(only_task) == 1
    assert only_task[0]["kind"] == "task_wave_executed"
    after = filter_events(events, after_seq=1)
    assert [e["seq"] for e in after] == [2, 3]


def test_memory_store_list_events_roundtrip():
    store = InMemoryMissionStore()
    result = MissionResult(
        mission_id="MS-persist-1",
        status="completed",
        mission_lead="commerce_agent",
        trace=TraceInfo(request_id="r", session_id="s"),
    )
    store.put(
        result,
        {
            "route": "lookup",
            "user_query": "net sales?",
            "events": [
                {"kind": "mission_created", "seq": 1, "family": "mission"},
                {"kind": "artifact_posted", "seq": 2, "family": "artifact"},
            ],
        },
    )
    assert store.get("MS-persist-1") is not None
    assert store.get_raw("MS-persist-1")["route"] == "lookup"
    events = store.list_events("MS-persist-1", family="artifact")
    assert len(events) == 1
    assert events[0]["kind"] == "artifact_posted"


def test_api_mission_events_endpoint(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)

    created = client.post(
        "/v1/missions",
        json={
            "query": "Why has CAC increased over the last three days?",
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
            "mode": "read_only",
        },
    )
    assert created.status_code == 200
    mission = created.json()
    mission_id = mission["mission_id"]
    assert mission.get("events")

    events_resp = client.get(f"/v1/missions/{mission_id}/events")
    assert events_resp.status_code == 200
    body = events_resp.json()
    assert body["mission_id"] == mission_id
    assert body["count"] >= 1
    assert all("kind" in e for e in body["events"])

    mission_family = client.get(f"/v1/missions/{mission_id}/events", params={"family": "mission"})
    assert mission_family.status_code == 200
    fam = mission_family.json()
    assert fam["count"] >= 1
    assert all(
        (e.get("family") == "mission") or str(e.get("kind", "")).startswith("mission_")
        for e in fam["events"]
    )

    missing = client.get("/v1/missions/does-not-exist/events")
    assert missing.status_code == 404

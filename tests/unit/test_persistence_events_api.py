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
    # Missing/zero seq only appears on the first page.
    with_zero = events + [{"kind": "legacy", "seq": 0, "family": "mission"}]
    assert len(filter_events(with_zero, after_seq=0)) == 4
    assert all(int(e.get("seq") or 0) > 1 for e in filter_events(with_zero, after_seq=1))


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


def test_api_mission_events_endpoint_404_for_unknown_mission(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    missing = client.get("/v1/missions/does-not-exist/events")
    assert missing.status_code == 404

"""Edge-case hardenings: cancel race, request_id, as_of window, typed status, full_* plan."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import seleric_swarm.main as main_mod
from seleric_swarm.api.async_missions import (
    cancel_running_mission,
    clear_cancel,
    is_cancel_requested,
    seed_running_mission,
)
from seleric_swarm.api.request_id import REQUEST_ID_HEADER
from seleric_swarm.api.status import coerce_typed_status
from seleric_swarm.contracts.lookup import MissionResult, TraceInfo
from seleric_swarm.coordinator.intake import apply_full_flags, normalize_query, resolve_mission_time_range
from seleric_swarm.main import app
from seleric_swarm.swarm.mission import SwarmMissionResult
from seleric_swarm.swarm.orchestrator import _swarm_mission_view


def test_store_refuses_overwrite_of_cancelled(runtime):
    mid = "MS-cancel-race"
    clear_cancel(mid)
    seed_running_mission(runtime, mission_id=mid, query="why?", request_id="r", session_id="s")
    cancel_running_mission(runtime, mission_id=mid, request_id="r")
    assert runtime.store.get(mid).status == "cancelled"

    late = MissionResult(
        mission_id=mid,
        status="completed",
        limitations=[],
        final_response="late success",
        trace=TraceInfo(request_id="r", session_id="s"),
    )
    runtime.store.put(late, {"route": "swarm", "status": "completed", "mission_id": mid})
    assert runtime.store.get(mid).status == "cancelled"
    assert runtime.store.get_raw(mid)["status"] == "cancelled"
    clear_cancel(mid)


def test_cancel_requested_reads_persisted_flag(runtime):
    mid = "MS-cancel-persist"
    clear_cancel(mid)
    seed_running_mission(runtime, mission_id=mid, query="why?", request_id="r", session_id="s")
    cancel_running_mission(runtime, mission_id=mid)
    clear_cancel(mid)  # clear process-local; store still cancelled
    assert is_cancel_requested(mid, runtime) is True
    clear_cancel(mid)


def test_mission_uses_x_request_id(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    created = client.post(
        "/v1/missions",
        headers={REQUEST_ID_HEADER: "corr-mission-99"},
        json={
            "query": "What were net sales yesterday?",
            "mode": "read_only",
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
        },
    )
    assert created.status_code == 200
    assert created.headers.get(REQUEST_ID_HEADER) == "corr-mission-99"
    body = created.json()
    trace = body.get("trace") or {}
    assert trace.get("request_id") == "corr-mission-99"


def test_as_of_extends_scenario_observation_window():
    scenario = {"observation_window": {"start": "2026-08-31", "end": "2026-09-02"}}
    # Scenario window kept; as_of past end extends end only
    tr = resolve_mission_time_range(scenario, timezone="Asia/Kolkata", as_of="2026-09-03")
    assert tr["end"] == "2026-09-03"
    assert tr["start"] == "2026-08-31"

    # as_of inside window does not shrink end
    tr_in = resolve_mission_time_range(scenario, timezone="Asia/Kolkata", as_of="2026-09-01")
    assert tr_in["end"] == "2026-09-02"
    assert tr_in["start"] == "2026-08-31"

    # No scenario window → fall back to normalized query window
    nq = normalize_query(
        "Why has CAC increased over the last three days?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    tr2 = resolve_mission_time_range(
        {}, timezone="Asia/Kolkata", as_of="2026-09-03", normalized=nq
    )
    assert tr2["end"] == "2026-09-03"
    assert tr2["start"] == "2026-08-31"


def test_swarm_mission_view_preserves_prototype_completed():
    result = SwarmMissionResult(
        mission_id="MS-proto",
        status="prototype_completed",
        query="why?",
        complexity="L4",
        initial_mission_lead="performance_agent",
        mission_lead="performance_agent",
        leadership_epoch=1,
        team=[],
        handoff_history=[],
        artifacts={},
        final_response="ok",
        limitations=["prototype_completed: synthetic evidence only"],
        synthetic=True,
        events=[],
        error_code=None,
    )
    view = _swarm_mission_view(result, "rid", "sid")
    assert view.status == "prototype_completed"
    assert coerce_typed_status("blocked") == "blocked"
    assert coerce_typed_status("cancelled") == "cancelled"
    assert coerce_typed_status("weird") == "partial"


def test_full_flags_folded_into_normalized_intents():
    nq = normalize_query("Why did CAC rise?", timezone="Asia/Kolkata", as_of="2026-09-03")
    intents = apply_full_flags(
        set(nq.intents),
        full_diagnostic=True,
        full_prediction=True,
        full_skeptic=True,
    )
    updated = nq.model_copy(update={"intents": sorted(intents)})
    assert "diagnostic" in updated.intents
    assert "predictive" in updated.intents

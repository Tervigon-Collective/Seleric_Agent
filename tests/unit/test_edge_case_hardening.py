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
from seleric_swarm.coordinator.intake import (
    apply_full_flags,
    normalize_query,
    resolve_mission_time_range,
)
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


def test_store_refuses_cancel_overwrite_of_completed(runtime):
    mid = "MS-cancel-after-done"
    clear_cancel(mid)
    done = MissionResult(
        mission_id=mid,
        status="completed",
        limitations=[],
        final_response="done",
        trace=TraceInfo(request_id="r", session_id="s"),
    )
    runtime.store.put(done, {"route": "swarm", "status": "completed", "mission_id": mid})
    cancelled = MissionResult(
        mission_id=mid,
        status="cancelled",
        limitations=["cancelled"],
        final_response=None,
        trace=TraceInfo(request_id="r", session_id="s"),
    )
    runtime.store.put(cancelled, {"route": "swarm", "status": "cancelled", "mission_id": mid})
    assert runtime.store.get(mid).status == "completed"
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


def test_swarm_mission_embeds_trace(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    created = client.post(
        "/v1/missions",
        headers={REQUEST_ID_HEADER: "swarm-corr-1"},
        json={
            "query": "Why has CAC increased over the last three days?",
            "mode": "read_only",
            "scenario_id": "cac_regression",
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body.get("route") == "swarm"
    assert (body.get("trace") or {}).get("request_id") == "swarm-corr-1"


def test_sync_reports_unresolvable_query_as_failed_mission(runtime, monkeypatch):
    """A query with no recognizable metric or analysis intent now resolves as
    a structured failed mission (status=failed, ROUTING_UNSUPPORTED) rather
    than an HTTP-level 400 — the request itself was well-formed, only the
    business question could not be answered."""
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(
        "/v1/missions",
        json={
            "query": "?????",
            "mode": "read_only",
            "scenario_id": "cac_regression",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "ROUTING_UNSUPPORTED"


def test_sync_passes_generated_session_id(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    created = client.post(
        "/v1/missions",
        json={
            "query": "What were net sales yesterday?",
            "mode": "read_only",
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
        },
    )
    assert created.status_code == 200
    body = created.json()
    trace = body.get("trace") or {}
    assert trace.get("session_id")
    assert len(str(trace["session_id"])) >= 8


@pytest.mark.asyncio
async def test_swarm_v1_accepts_execution_mode(runtime, monkeypatch):
    """dispatch must not TypeError when swarm_workflow=swarm_v1 + execution_mode."""
    from seleric_swarm.orchestration import dispatch as disp

    monkeypatch.setattr(runtime.settings, "swarm_workflow", "swarm_v1")
    out = await disp.run_any_mission(
        runtime,
        query="Why has CAC increased over the last three days?",
        as_of="2026-09-03",
        scenario_id="cac_regression",
        execution_mode="fixture",
        full_diagnostic=True,
    )
    assert out["route"] == "swarm"
    assert out["result"]["status"] in {
        "completed",
        "prototype_completed",
        "partial",
        "failed",
    }


async def test_as_of_extends_scenario_observation_window():
    scenario = {"observation_window": {"start": "2026-08-31", "end": "2026-09-02"}}
    # Scenario window kept; as_of past end extends end only
    tr = resolve_mission_time_range(scenario, timezone="Asia/Kolkata", as_of="2026-09-03")
    assert tr["end"] == "2026-09-03"
    assert tr["start"] == "2026-08-31"
    assert tr["observation_end"] == "2026-09-02"

    # Invalid as_of is ignored (does not corrupt the window)
    tr_bad = resolve_mission_time_range(scenario, timezone="Asia/Kolkata", as_of="not-a-date")
    assert tr_bad["end"] == "2026-09-02"
    assert tr_bad["start"] == "2026-08-31"

    # as_of inside window does not shrink end
    tr_in = resolve_mission_time_range(scenario, timezone="Asia/Kolkata", as_of="2026-09-01")
    assert tr_in["end"] == "2026-09-02"
    assert tr_in["start"] == "2026-08-31"

    # No scenario window → fall back to normalized query window
    nq = await normalize_query(
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


async def test_full_flags_folded_into_normalized_intents():
    nq = await normalize_query("Why did CAC rise?", timezone="Asia/Kolkata", as_of="2026-09-03")
    intents = apply_full_flags(
        set(nq.intents),
        full_diagnostic=True,
        full_prediction=True,
        full_skeptic=True,
    )
    updated = nq.model_copy(update={"intents": sorted(intents)})
    assert "diagnostic" in updated.intents
    assert "predictive" in updated.intents


def test_unknown_timezone_falls_back_instead_of_crashing(runtime, monkeypatch):
    """An invalid IANA zone used to raise ZoneInfoNotFoundError -> 500."""
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(
        "/v1/missions",
        json={
            "query": "why did CAC increase?",
            "scope": {"timezone": "Not/AZone", "as_of": "2026-09-03"},
            "scenario_id": "cac_regression",
        },
    )
    assert resp.status_code == 200


def test_non_string_as_of_is_rejected_not_crashed(runtime, monkeypatch):
    """scope.as_of as a non-string used to raise TypeError deep in intake -> 500."""
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(
        "/v1/missions",
        json={
            "query": "why did CAC increase?",
            "scope": {"as_of": 12345},
            "scenario_id": "cac_regression",
        },
    )
    assert resp.status_code == 400


def test_as_of_year_out_of_range_is_rejected_not_crashed(runtime, monkeypatch):
    """as_of near date.min previously overflowed 'today - timedelta(days=N)'
    arithmetic in resolve_time_range -> unhandled OverflowError -> 500."""
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(
        "/v1/missions",
        json={
            "query": "why did CAC increase in the last three days?",
            "scope": {"as_of": "0001-01-01"},
            "scenario_id": "cac_regression",
        },
    )
    assert resp.status_code == 400


def test_malformed_as_of_string_is_rejected_not_swallowed(runtime, monkeypatch):
    """An unparseable date string previously surfaced as a misleading
    status=failed / LLM_UNAVAILABLE mission instead of a clean 400."""
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(
        "/v1/missions",
        json={
            "query": "What were net sales yesterday?",
            "scope": {"as_of": "not-a-date"},
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_library_swarm_requires_scenario_id(runtime):
    from seleric_swarm.coordinator.graph import run_swarm_v2_mission
    from seleric_swarm.swarm.orchestrator import run_swarm_mission

    with pytest.raises(ValueError, match="scenario_id is required"):
        await run_swarm_mission(runtime, query="why did CAC increase?")
    with pytest.raises(ValueError, match="scenario_id is required"):
        await run_swarm_v2_mission(runtime, query="why did CAC increase?")


def test_failed_cancel_clears_process_flag(runtime):
    mid = "MS-cancel-flag-leak"
    clear_cancel(mid)
    done = MissionResult(
        mission_id=mid,
        status="completed",
        limitations=[],
        final_response="done",
        trace=TraceInfo(request_id="r", session_id="s"),
    )
    runtime.store.put(done, {"route": "swarm", "status": "completed", "mission_id": mid})
    with pytest.raises(ValueError, match="not cancellable"):
        cancel_running_mission(runtime, mission_id=mid)
    assert is_cancel_requested(mid) is False
    clear_cancel(mid)

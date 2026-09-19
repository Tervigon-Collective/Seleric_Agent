"""Dispatch routing regressions for lookup vs swarm.

route_for is LLM-classification-driven (coordinator.classify_swarm), so these
run against the `runtime` fixture's fake LLM adapter rather than calling it
with no runtime.
"""

from __future__ import annotations

import pytest

from seleric_swarm.orchestration import dispatch
from seleric_swarm.orchestration.dispatch import route_for


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected",
    [
        ("What were net sales yesterday?", "lookup"),
        ("How many orders did we get on 2026-09-02?", "lookup"),
        ("Compare Meta vs Google CAC over the last three days", "lookup"),
        ("Why has CAC increased over the last three days?", "swarm"),
        ("how are we doing today?", "swarm"),
        ("what happens if this continues?", "swarm"),
        ("what should we do about rising CAC?", "swarm"),
        ("Explain the root cause of mobile LCP degradation", "swarm"),
        ("tell me what the blended CAC was on 2026-09-02", "lookup"),
        ("Compare Meta vs Google CAC increase over three days", "lookup"),
    ],
)
async def test_route_for_lookup_vs_swarm(runtime, query, expected):
    assert await route_for(runtime, query=query) == expected


@pytest.mark.asyncio
async def test_greeting_completes_without_classifier_or_swarm(runtime, monkeypatch):
    async def fail_route(*args, **kwargs):
        raise AssertionError("a greeting must not invoke business routing")

    monkeypatch.setattr(dispatch, "route_for", fail_route)

    response = await dispatch.run_any_mission(
        runtime,
        query="Hi!",
        mission_id="MS-greeting",
        request_id="request-greeting",
        session_id="thread-greeting",
    )

    assert response["route"] == "conversation"
    assert response["result"]["status"] == "completed"
    assert "What would you like to investigate?" in response["result"]["final_response"]
    assert runtime.store.get("MS-greeting").status == "completed"
    # The office UI (api/office/gateway.py) reads trace/session off the raw
    # persisted state, not off this handler's return value -- a mission that
    # only ever populates it on `result` and not on the raw dict is invisible
    # to the office snapshot's trace/session fields.
    raw = runtime.store.get_raw("MS-greeting")
    assert raw["trace"] == {"request_id": "request-greeting", "session_id": "thread-greeting"}


@pytest.mark.asyncio
async def test_business_overview_returns_without_classifier_or_swarm(runtime, monkeypatch):
    async def fail_route(*args, **kwargs):
        raise AssertionError("a broad overview must not wait for business routing")

    async def no_snapshots(*args, **kwargs):
        return [], [("commerce", "no snapshot available")]

    monkeypatch.setattr(dispatch, "route_for", fail_route)
    monkeypatch.setattr(dispatch, "read_overview_snapshots", no_snapshots)

    response = await dispatch.run_any_mission(
        runtime,
        query="How are we doing today?",
        mission_id="MS-overview",
        request_id="request-overview",
        session_id="thread-overview",
    )

    assert response["workflow"] == "overview_snapshot"
    assert response["result"]["status"] == "partial"
    assert "couldn’t load a current business overview" in response["result"]["final_response"]
    assert runtime.store.get("MS-overview").status == "partial"
    raw = runtime.store.get_raw("MS-overview")
    assert raw["trace"] == {"request_id": "request-overview", "session_id": "thread-overview"}


@pytest.mark.asyncio
async def test_lookup_mission_forwards_context_bundle(runtime, monkeypatch):
    from seleric_swarm.contracts.lookup import EvidenceView, MissionResult, TraceInfo

    seen: dict = {}

    async def fake_lookup(*args, **kwargs):
        seen["context_bundle"] = kwargs.get("context_bundle")
        return MissionResult(
            mission_id="MS-ctx",
            status="completed",
            query_class="lookup",
            mission_lead="commerce_agent",
            initial_mission_lead="commerce_agent",
            evidence=[
                EvidenceView(
                    evidence_id="EV-1",
                    metric_or_fact="metric.gross_sales",
                    value=4789.73,
                    time_range={"start": "2026-09-18", "end": "2026-09-18"},
                    source="test",
                )
            ],
            limitations=[],
            final_response="gross sales: 4789.73",
            trace=TraceInfo(request_id="r", session_id="s"),
        )

    monkeypatch.setattr(dispatch, "run_lookup_fast_path", fake_lookup)
    bundle = {
        "recent_messages": [
            {"role": "USER", "parts": [{"type": "TEXT", "content": "gross sales today"}]}
        ]
    }

    response = await dispatch.run_any_mission(
        runtime,
        query="gross sale",
        mission_id="MS-ctx",
        request_id="request-ctx",
        session_id="thread-ctx",
        context_bundle=bundle,
    )

    assert response["route"] == "lookup"
    assert seen["context_bundle"] is bundle


@pytest.mark.asyncio
async def test_time_followup_is_not_swallowed_as_conversation(runtime, monkeypatch):
    from seleric_swarm.contracts.lookup import MissionResult, TraceInfo

    async def fake_chat(*args, **kwargs):
        return "Hello! What would you like to investigate?"

    async def fake_lookup(*args, **kwargs):
        return MissionResult(
            mission_id="MS-follow",
            status="completed",
            query_class="lookup",
            mission_lead="commerce_agent",
            initial_mission_lead="commerce_agent",
            evidence=[],
            limitations=[],
            final_response="gross sales: 4789.73 (2026-09-17)",
            trace=TraceInfo(request_id="r", session_id="s"),
        )

    monkeypatch.setattr(dispatch, "classify_conversational_via_llm", fake_chat)
    monkeypatch.setattr(dispatch, "run_lookup_fast_path", fake_lookup)
    bundle = {
        "recent_messages": [
            {"role": "USER", "parts": [{"type": "TEXT", "content": "gross sales today"}]}
        ]
    }

    response = await dispatch.run_any_mission(
        runtime,
        query="yesterday?",
        mission_id="MS-follow",
        request_id="request-follow",
        session_id="thread-follow",
        context_bundle=bundle,
    )

    assert response["route"] == "lookup"
    assert "4789.73" in response["result"]["final_response"]


@pytest.mark.asyncio
async def test_run_any_mission_accepts_v3_style_kwargs_on_swarm_route(runtime, monkeypatch):
    """Regression, found live 2026-09-19: ``main.py`` now calls
    ``run_v3_mission``/``run_any_mission`` with the same unified kwarg set
    (``workspace_id``/``owner_user_id``/``thread_id``/``run_id``) so it can
    pick either at request time via ``v3_agent_enabled``. Those extra kwargs
    used to fall into ``run_any_mission``'s ``**swarm_only`` and get
    forwarded straight into ``run_swarm_v2_mission(**swarm_only)``, which
    doesn't accept them -- a live ``TypeError`` on every swarm-routed
    mission, not just a theoretical one."""

    async def force_swarm(*args, **kwargs):
        return "swarm"

    async def fake_swarm(*args, **kwargs):
        from seleric_swarm.swarm.mission import SwarmMissionResult

        return SwarmMissionResult(
            mission_id="MS-kwargs",
            status="completed",
            query="why did CAC rise?",
            complexity="L0",
            initial_mission_lead="coordinator_agent",
            mission_lead="coordinator_agent",
            leadership_epoch=0,
            team=[],
            handoff_history=[],
            artifacts={},
            final_response="ok",
        )

    monkeypatch.setattr(dispatch, "route_for", force_swarm)
    monkeypatch.setattr("seleric_swarm.coordinator.graph.run_swarm_v2_mission", fake_swarm)

    response = await dispatch.run_any_mission(
        runtime,
        query="why did CAC rise?",
        mission_id="MS-kwargs",
        request_id="request-kwargs",
        session_id="thread-kwargs",
        workspace_id="ws-1",
        owner_user_id="user-1",
        thread_id="thread-kwargs",
        run_id="request-kwargs",
    )

    assert response["route"] == "swarm"

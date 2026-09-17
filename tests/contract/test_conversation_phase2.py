"""Phase 2 incremental activity event and SSE contract tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.conversations import (
    ActivityEvent,
    ActivityEventSink,
    EventVisibility,
    InMemoryEventNotifier,
    Principal,
    PrincipalAuthMethod,
    Run,
    RunStatus,
    Thread,
    event_for_principal,
    event_type_for_mission_kind,
    map_mission_event,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories


def _records():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(
        Thread(workspace_id="workspace_1", owner_user_id="user_1")
    )
    run = repositories.runs.create(
        Run(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            requested_by_user_id=thread.owner_user_id,
            mission_id="mission_1",
        )
    )
    return repositories, thread, run


def _event(run: Run, event_type: str, **kwargs) -> ActivityEvent:
    return ActivityEvent(
        thread_id=run.thread_id,
        workspace_id=run.workspace_id,
        run_id=run.id,
        owner_user_id=run.requested_by_user_id,
        event_type=event_type,
        **kwargs,
    )


def test_versioned_mapping_covers_lifecycle_and_specialists():
    _, _, run = _records()
    assert event_type_for_mission_kind("task_plan_created") == "plan.created"
    assert event_type_for_mission_kind("tool_query_done") == "tool.completed"
    assert event_type_for_mission_kind("anomaly_done") == "agent.completed"

    mapped = map_mission_event(
        {
            "kind": "anomaly_done",
            "seq": 7,
            "specialist": "anomaly",
            "summary": "Analysis complete",
            "chain_of_thought": "private scratchpad",
            "prompt": "private prompt",
        },
        run,
    )
    assert mapped is not None
    assert mapped.event_type == "agent.completed"
    assert mapped.actor_id == "anomaly"
    assert mapped.metadata["mapping_version"] == "1"
    assert "chain_of_thought" not in mapped.payload
    assert "prompt" not in mapped.payload


def test_visibility_redaction_and_allow_list_exclude_private_reasoning():
    _, _, run = _records()
    principal = Principal(
        principal_id="user_1",
        workspace_id="workspace_1",
        user_id="user_1",
        authenticated=True,
        auth_method=PrincipalAuthMethod.SHARED_API_KEY,
    )
    event = _event(
        run,
        "decision.recorded",
        summary="Contact analyst@example.com",
        payload={
            "decision": "Investigate",
            "note": "Bearer abcdefghijklmnop",
            "reasoning": "private chain of thought",
            "prompt": "system prompt",
        },
        metadata={"trace": "private"},
    )
    public = event_for_principal(event, principal)
    assert public is not None
    assert public.summary == "Contact [REDACTED_EMAIL]"
    assert public.payload["note"] == "Bearer [REDACTED]"
    assert set(public.payload) == {"decision", "note"}
    assert public.metadata == {}

    internal = event.model_copy(update={"visibility": EventVisibility.INTERNAL})
    assert event_for_principal(internal, principal) is None


def test_append_is_monotonic_idempotent_and_cursor_resumable():
    repositories, _, run = _records()
    first = repositories.runs.append_event(_event(run, "run.started"))
    duplicate = repositories.runs.append_event(first.model_copy(update={"sequence": 0}))
    second = repositories.runs.append_event(_event(run, "plan.created"))
    assert duplicate == first
    assert [first.sequence, second.sequence] == [1, 2]
    assert repositories.runs.list_events(run.id, after_sequence=1) == [second]
    with pytest.raises(ValueError, match="monotonic"):
        repositories.runs.append_event(_event(run, "warning.created", sequence=1))


async def test_sink_notifier_delivers_intermediate_event():
    repositories, _, run = _records()
    notifier = InMemoryEventNotifier()
    sink = ActivityEventSink(repositories.runs, notifier)

    waiter = asyncio.create_task(notifier.wait(run.id, 0, 1.0))
    await asyncio.sleep(0)
    appended = sink.emit(run, "agent.started", payload={"agent": "anomaly"})
    await asyncio.wait_for(waiter, timeout=0.5)

    assert appended.sequence == 1
    assert repositories.runs.list_events(run.id) == [appended]


def _client():
    repositories, thread, run = _records()
    notifier = InMemoryEventNotifier()
    runtime = SimpleNamespace(
        conversations=repositories,
        activity_events=ActivityEventSink(repositories.runs, notifier),
    )
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(conversations_api.router)
    app.add_middleware(
        ApiSecurityMiddleware,
        api_key="shared-secret",
        rate_limit_enabled=False,
        default_workspace_id="workspace_1",
        default_user_id="user_1",
        trust_identity_headers=True,
    )
    return TestClient(app), repositories, thread, run


def _headers(user_id: str = "user_1"):
    return {
        "X-API-Key": "shared-secret",
        "X-Workspace-ID": "workspace_1",
        "X-User-ID": user_id,
    }


def test_event_endpoints_require_authentication_and_ownership():
    client, _, _, run = _client()
    assert client.get(f"/v1/runs/{run.id}/events").status_code == 401
    assert (
        client.get(f"/v1/runs/{run.id}/events", headers=_headers("user_2")).status_code
        == 404
    )
    assert client.get(f"/v1/runs/{run.id}/events", headers=_headers()).status_code == 200


def test_sse_resumes_from_query_or_last_event_id():
    client, repositories, _, run = _client()
    repositories.runs.append_event(_event(run, "run.started"))
    repositories.runs.append_event(_event(run, "agent.started"))
    repositories.runs.append_event(_event(run, "run.completed"))
    repositories.runs.update(run.model_copy(update={"status": RunStatus.COMPLETED}))

    query = client.get(
        f"/v1/runs/{run.id}/events/stream?after_sequence=1",
        headers=_headers(),
    )
    assert query.status_code == 200
    assert "id: 1\n" not in query.text
    assert "id: 2\n" in query.text and "id: 3\n" in query.text
    assert '"type":"agent.started"' in query.text

    header = client.get(
        f"/v1/runs/{run.id}/events/stream",
        headers={**_headers(), "Last-Event-ID": "2"},
    )
    assert "id: 2\n" not in header.text
    assert "id: 3\n" in header.text

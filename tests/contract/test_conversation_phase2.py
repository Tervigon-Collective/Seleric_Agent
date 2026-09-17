"""Phase 2 incremental activity event and SSE contract tests."""

from __future__ import annotations

import asyncio
import queue
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.conversations import (
    ActivityEvent,
    ActivityEventSink,
    EventVisibility,
    InMemoryEventNotifier,
    Principal,
    PrincipalAuthMethod,
    RedisEventNotifier,
    Run,
    RunStatus,
    Thread,
    event_for_principal,
    event_type_for_mission_kind,
    map_mission_event,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.coordinator.observability.events import MissionEventEmitter
from seleric_swarm.orchestration.graph import _emit_lookup_event
from seleric_swarm.swarm.blackboard import (
    Blackboard,
    observe_blackboard_events,
    observe_mission_events,
)


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
    assert event_type_for_mission_kind("mission_cancelled") == "run.cancelled"

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


def test_thread_cursor_is_monotonic_across_runs():
    repositories, thread, first_run = _records()
    second_run = repositories.runs.create(
        Run(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            requested_by_user_id=thread.owner_user_id,
        )
    )
    first = repositories.runs.append_event(_event(first_run, "run.started"))
    second = repositories.runs.append_event(_event(second_run, "run.started"))
    third = repositories.runs.append_event(_event(first_run, "run.completed"))

    assert [first.sequence, second.sequence, third.sequence] == [1, 1, 2]
    assert [first.thread_sequence, second.thread_sequence, third.thread_sequence] == [1, 2, 3]
    assert repositories.runs.list_thread_events(thread.id, after_sequence=1) == [second, third]


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


def test_blackboard_observer_persists_incrementally_and_backfill_is_idempotent():
    repositories, _, run = _records()
    sink = ActivityEventSink(repositories.runs, InMemoryEventNotifier())
    source_events: list[dict[str, object]] = []

    def observe(event: dict[str, object]) -> None:
        source_events.append(event)
        sink.ingest_mission_events(run, [event])

    with observe_blackboard_events(observe):
        blackboard = Blackboard(run.mission_id or "mission_1")
        emitted = MissionEventEmitter(blackboard).emit(
            "task_plan_created", task_id="task_1"
        )

    persisted = repositories.runs.list_events(run.id)
    assert len(persisted) == 1
    assert persisted[0].event_type == "plan.created"
    assert emitted in blackboard.events

    sink.ingest_mission_events(run, source_events)
    assert repositories.runs.list_events(run.id) == persisted


class _FakePubSub:
    def __init__(self, client):
        self.client = client
        self.channels: set[str] = set()
        self.messages: queue.Queue[dict[str, object]] = queue.Queue()

    def subscribe(self, channel: str) -> None:
        self.channels.add(channel)
        self.client.subscribers.setdefault(channel, []).append(self)

    def unsubscribe(self, channel: str) -> None:
        self.channels.discard(channel)
        self.client.subscribers.get(channel, []).remove(self)

    def get_message(self, *, ignore_subscribe_messages: bool, timeout: float):
        del ignore_subscribe_messages
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        return None


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.subscribers: dict[str, list[_FakePubSub]] = {}

    def get(self, key: str):
        return self.values.get(key)

    def eval(self, script: str, key_count: int, key: str, channel: str, value: int):
        del script, key_count
        current = int(self.values.get(key, "0"))
        self.values[key] = str(max(current, value))
        for subscriber in self.subscribers.get(channel, []):
            subscriber.messages.put({"data": str(value)})
        return max(current, value)

    def pubsub(self):
        return _FakePubSub(self)


async def test_redis_notifier_wakes_waiters_and_remembers_high_water_mark():
    notifier = RedisEventNotifier(_FakeRedis(), prefix="test:")
    waiter = asyncio.create_task(notifier.wait("run_1", 0, 1.0))
    await asyncio.sleep(0.01)
    notifier.publish("run_1", 1)
    await asyncio.wait_for(waiter, timeout=0.5)

    # A subscriber that starts after publish still observes the durable version.
    await asyncio.wait_for(notifier.wait("run_1", 0, 1.0), timeout=0.1)
    notifier.publish("run_1", 3)
    notifier.publish("run_1", 2)
    assert notifier._version("run_1") == 3


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


async def test_sse_delivers_notified_event_before_run_completion():
    repositories, _, run = _records()
    running = repositories.runs.update(run.model_copy(update={"status": RunStatus.RUNNING}))
    notifier = InMemoryEventNotifier()
    runtime = SimpleNamespace(
        conversations=repositories,
        activity_events=ActivityEventSink(repositories.runs, notifier),
    )
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime

    async def receive():
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/v1/runs/{run.id}/events/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
            "scheme": "http",
            "app": app,
        },
        receive=receive,
    )
    request.state.principal = Principal(
        principal_id="user_1",
        workspace_id="workspace_1",
        user_id="user_1",
        authenticated=True,
        auth_method=PrincipalAuthMethod.SERVICE,
    )
    response = await conversations_api.stream_run_events(
        run.id, request, after_sequence=0, heartbeat_seconds=0.05
    )
    iterator = response.body_iterator
    assert await anext(iterator) == ": heartbeat\n\n"

    next_frame = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)
    runtime.activity_events.emit(running, "agent.started", payload={"agent": "diagnostic"})
    frame = await asyncio.wait_for(next_frame, timeout=0.5)
    assert "event: agent.started" in frame
    assert repositories.runs.get(run.id).status is RunStatus.RUNNING
    await iterator.aclose()


async def test_lookup_event_is_visible_before_run_completion():
    repositories, _, run = _records()
    running = repositories.runs.update(run.model_copy(update={"status": RunStatus.RUNNING}))
    notifier = InMemoryEventNotifier()
    sink = ActivityEventSink(repositories.runs, notifier)
    runtime = SimpleNamespace(conversations=repositories, activity_events=sink)
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime

    async def receive():
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/v1/runs/{run.id}/events/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
            "scheme": "http",
            "app": app,
        },
        receive=receive,
    )
    request.state.principal = Principal(
        principal_id="user_1",
        workspace_id="workspace_1",
        user_id="user_1",
        authenticated=True,
        auth_method=PrincipalAuthMethod.SERVICE,
    )
    response = await conversations_api.stream_run_events(
        run.id, request, after_sequence=0, heartbeat_seconds=0.05
    )
    iterator = response.body_iterator
    assert await anext(iterator) == ": heartbeat\n\n"
    next_frame = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)

    def persist(source: dict[str, object]) -> None:
        sink.ingest_mission_events(running, [source])

    with observe_mission_events(persist):
        _emit_lookup_event(
            {"mission_id": run.mission_id, "events": []},
            "claim_validated",
            claim_count=1,
        )

    frame = await asyncio.wait_for(next_frame, timeout=0.5)
    assert "event: decision.recorded" in frame
    assert repositories.runs.get(run.id).status is RunStatus.RUNNING
    await iterator.aclose()


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

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.conversations.contracts import (
    Message,
    MessagePart,
    MessageRole,
    Run,
    Thread,
)
from seleric_swarm.conversations.events import ActivityEventSink
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.persistence.memory import InMemoryMissionStore, filter_events


def test_unit_of_work_rolls_back_every_submission_stage() -> None:
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))

    for fail_after in range(1, 5):
        run = Run(
            id=f"run-{fail_after}",
            thread_id=thread.id,
            workspace_id="w",
            requested_by_user_id="u",
        )
        message = Message(
            id=f"message-{fail_after}",
            thread_id=thread.id,
            workspace_id="w",
            user_id="u",
            role=MessageRole.USER,
            parts=[MessagePart(type="TEXT", content="query")],
            run_id=run.id,
        )
        try:
            with repositories.transaction() as writes:
                writes.runs.create(run)
                if fail_after == 1:
                    raise RuntimeError("injected after run")
                writes.messages.create(message)
                if fail_after == 2:
                    raise RuntimeError("injected after message")
                writes.runs.add_outbox(run.id)
                if fail_after == 3:
                    raise RuntimeError("injected after outbox")
                writes.threads.update(thread.model_copy(update={"title": "changed"}))
                raise RuntimeError("injected after thread")
        except RuntimeError:
            pass
        assert repositories.runs.get(run.id) is None
        assert repositories.messages.get(message.id) is None
        assert run.id not in repositories.runs.list_pending_outbox()
        assert repositories.threads.get(thread.id).title is None


def test_event_publish_failure_is_deterministically_recoverable() -> None:
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    run = repositories.runs.create(
        Run(thread_id=thread.id, workspace_id="w", requested_by_user_id="u")
    )

    class BrokenNotifier:
        def publish(self, _run_id: str, _sequence: int) -> None:
            raise ConnectionError("injected publication failure")

    event = ActivityEventSink(repositories.runs, BrokenNotifier()).emit(
        run, "run.queued"
    )
    assert repositories.runs.list_events(run.id) == [event]


def test_mission_event_cursors_advance_for_bad_source_sequences() -> None:
    events = [
        {"kind": "a", "seq": 0},
        {"kind": "b", "seq": 2},
        {"kind": "c", "seq": 2},
        {"kind": "d", "seq": 1},
    ]
    first = filter_events(events, limit=2)
    assert [event["seq"] for event in first] == [1, 2]
    assert [event["seq"] for event in filter_events(events, after_seq=2)] == [3, 4]


def test_queue_failure_leaves_durable_submission_outbox() -> None:
    repositories = build_in_memory_repositories()

    class BrokenQueue:
        async def enqueue(self, _run_id: str) -> None:
            raise ConnectionError("injected queue failure")

    runtime = SimpleNamespace(
        conversations=repositories,
        store=InMemoryMissionStore(),
        run_queue=BrokenQueue(),
    )
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(conversations_api.router)
    app.add_middleware(
        ApiSecurityMiddleware,
        rate_limit_enabled=False,
        default_workspace_id="workspace",
        default_user_id="user",
    )
    client = TestClient(app)
    thread_id = client.post("/v1/threads", json={}).json()["id"]
    response = client.post(
        f"/v1/threads/{thread_id}/messages",
        json={"parts": [{"type": "TEXT", "content": "recover me"}]},
    )
    assert response.status_code == 202
    assert repositories.runs.list_pending_outbox() == [response.json()["run_id"]]

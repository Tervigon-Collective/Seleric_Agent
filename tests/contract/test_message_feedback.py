"""Sprint 4: append-only reader feedback on assistant answers."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.conversations import Message, MessageFeedback, MessagePart, MessagePartType, MessageRole, Thread
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.persistence.memory import InMemoryMissionStore


class _RecordingQueue:
    def __init__(self) -> None:
        self.run_ids: list[str] = []

    async def enqueue(self, run_id: str) -> None:
        self.run_ids.append(run_id)

    async def close(self) -> None:
        return None


def _client(monkeypatch) -> TestClient:
    repositories = build_in_memory_repositories()
    runtime = SimpleNamespace(
        conversations=repositories,
        store=InMemoryMissionStore(),
        run_queue=_RecordingQueue(),
    )

    async def no_execution(*args, **kwargs):
        return None

    monkeypatch.setattr(conversations_api, "_execute_submission", no_execution)
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(conversations_api.router)
    app.add_middleware(
        ApiSecurityMiddleware,
        rate_limit_enabled=False,
        default_workspace_id="workspace_1",
        default_user_id="user_1",
    )
    return TestClient(app)


def _message(repositories, thread, role=MessageRole.ASSISTANT) -> Message:
    return repositories.messages.create(
        Message(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            user_id=thread.owner_user_id,
            role=role,
            run_id=None,
            parts=[MessagePart(type=MessagePartType.TEXT, content="Net sales were X.")],
        )
    )


def test_feedback_submit_supersedes_and_lists():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    message = _message(repositories, thread)
    first = repositories.feedback.submit(
        MessageFeedback(
            workspace_id="w", owner_user_id="u", thread_id=thread.id,
            message_id=message.id, rating="down", note="wrong metric",
        )
    )
    second = repositories.feedback.submit(
        MessageFeedback(
            workspace_id="w", owner_user_id="u", thread_id=thread.id,
            message_id=message.id, rating="up",
        )
    )
    assert second.supersedes_id == first.id
    latest = repositories.feedback.latest_for_message(message.id, "w", "u")
    assert latest is not None and latest.id == second.id and latest.rating == "up"
    assert [item.id for item in repositories.feedback.list_for_thread(thread.id, "w", "u")] == [
        second.id, first.id,
    ]
    assert repositories.feedback.latest_for_message("missing", "w", "u") is None


def test_feedback_note_is_bounded():
    with pytest.raises(ValidationError):
        MessageFeedback(
            workspace_id="w", owner_user_id="u", thread_id="t",
            message_id="m", rating="down", note="x" * 2001,
        )


def test_feedback_api_round_trip(monkeypatch):
    client = _client(monkeypatch)
    thread_id = client.post("/v1/threads", json={}).json()["id"]
    assert client.get(f"/v1/threads/{thread_id}/messages").json() == []
    created = client.post(
        f"/v1/threads/{thread_id}/messages",
        json={"parts": [{"type": "TEXT", "content": "Why did churn increase?"}]},
    )
    assert created.status_code == 202
    stored = client.get(f"/v1/threads/{thread_id}/messages").json()
    assistant_id = next(m["id"] for m in stored if m["role"] == "ASSISTANT")

    posted = client.post(
        f"/v1/threads/{thread_id}/messages/{assistant_id}/feedback",
        json={"rating": "down", "note": "Metric looks wrong"},
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["rating"] == "down"

    fetched = client.get(f"/v1/threads/{thread_id}/messages/{assistant_id}/feedback")
    assert fetched.status_code == 200
    assert fetched.json()["note"] == "Metric looks wrong"

    assert client.post(
        f"/v1/threads/{thread_id}/messages/{assistant_id}/feedback",
        json={"rating": "sideways"},
    ).status_code == 422
    assert client.post(
        "/v1/threads/nope/messages/nope/feedback",
        json={"rating": "up"},
    ).status_code == 404

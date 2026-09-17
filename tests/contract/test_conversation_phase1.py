"""Focused Phase 1 conversation repository and API tests."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.conversations import (
    ActivityEvent,
    Artifact,
    Message,
    MessagePart,
    MessagePartType,
    MessageRole,
    Run,
    Thread,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.persistence.memory import InMemoryMissionStore


def test_in_memory_repositories_preserve_normalized_contracts():
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
    message = repositories.messages.create(
        Message(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            user_id=thread.owner_user_id,
            role=MessageRole.USER,
            run_id=run.id,
            parts=[MessagePart(type=MessagePartType.TEXT, content="Investigate churn")],
        )
    )
    first = repositories.runs.append_event(
        ActivityEvent(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            run_id=run.id,
            event_type="run.queued",
        )
    )
    second = repositories.runs.append_event(
        ActivityEvent(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            run_id=run.id,
            event_type="run.started",
        )
    )
    artifact = repositories.artifacts.put(
        Artifact(
            id="artifact_1",
            workspace_id=thread.workspace_id,
            mission_id=run.mission_id,
            thread_id=thread.id,
            run_id=run.id,
            artifact_type="anomaly",
            payload={"artifact_id": "artifact_1", "score": 0.91, "evidence_refs": ["EV-1"]},
        )
    )

    assert repositories.messages.get(message.id) == message
    assert [event.sequence for event in repositories.runs.list_events(run.id)] == [1, 2]
    assert first.sequence == 1 and second.sequence == 2
    assert repositories.artifacts.list_for_mission("mission_1") == [artifact]


def _client(monkeypatch) -> tuple[TestClient, object]:
    repositories = build_in_memory_repositories()
    runtime = SimpleNamespace(
        conversations=repositories,
        store=InMemoryMissionStore(),
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
    return TestClient(app), repositories


def test_thread_crud_and_message_submission_are_owner_scoped(monkeypatch):
    client, repositories = _client(monkeypatch)
    created = client.post("/v1/threads", json={"title": "Retention"}).json()
    thread_id = created["id"]

    response = client.post(
        f"/v1/threads/{thread_id}/messages",
        json={"parts": [{"type": "TEXT", "content": "Why did churn increase?"}]},
    )
    assert response.status_code == 202
    assert set(response.json()) == {"message_id", "run_id", "mission_id"}
    assert len(client.get(f"/v1/threads/{thread_id}/messages").json()) == 1
    renamed = client.patch(
        f"/v1/threads/{thread_id}", json={"title": "Retention diagnosis"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Retention diagnosis"
    assert client.post(f"/v1/threads/{thread_id}/archive").json()["status"] == "ARCHIVED"
    assert thread_id not in {thread["id"] for thread in client.get("/v1/threads").json()}

    foreign = repositories.threads.create(
        Thread(workspace_id="workspace_2", owner_user_id="user_2")
    )
    assert client.get(f"/v1/threads/{foreign.id}").status_code == 404


def test_first_message_names_an_untitled_thread(monkeypatch):
    client, _ = _client(monkeypatch)
    thread_id = client.post("/v1/threads", json={}).json()["id"]
    response = client.post(
        f"/v1/threads/{thread_id}/messages",
        json={"parts": [{"type": "TEXT", "content": "Why did CAC increase this week?"}]},
    )
    assert response.status_code == 202
    assert (
        client.get(f"/v1/threads/{thread_id}").json()["title"]
        == "Why did CAC increase this week?"
    )


def test_archived_thread_rejects_new_messages(monkeypatch):
    client, _ = _client(monkeypatch)
    thread_id = client.post("/v1/threads", json={}).json()["id"]
    client.post(f"/v1/threads/{thread_id}/archive")
    response = client.post(
        f"/v1/threads/{thread_id}/messages",
        json={"parts": [{"type": "TEXT", "content": "Too late"}]},
    )
    assert response.status_code == 409

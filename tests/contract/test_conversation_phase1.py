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


class _RecordingQueue:
    def __init__(self) -> None:
        self.run_ids: list[str] = []

    async def enqueue(self, run_id: str) -> None:
        self.run_ids.append(run_id)

    async def close(self) -> None:
        return None


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
            classification="derived",
            evidence_ids=["EV-1"],
            provenance={"evidence_ids": ["EV-1"], "calculation_version": "test-v1"},
        )
    )

    assert repositories.messages.get(message.id) == message
    assert [event.sequence for event in repositories.runs.list_events(run.id)] == [1, 2]
    assert first.sequence == 1 and second.sequence == 2
    assert repositories.artifacts.list_for_mission("mission_1") == [artifact]


def _client(monkeypatch) -> tuple[TestClient, object, object]:
    repositories = build_in_memory_repositories()
    queue = _RecordingQueue()
    runtime = SimpleNamespace(
        conversations=repositories,
        store=InMemoryMissionStore(),
        run_queue=queue,
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
    return TestClient(app), repositories, runtime


def test_thread_crud_and_message_submission_are_owner_scoped(monkeypatch):
    client, repositories, runtime = _client(monkeypatch)
    created = client.post("/v1/threads", json={"title": "Retention"}).json()
    thread_id = created["id"]

    response = client.post(
        f"/v1/threads/{thread_id}/messages",
        json={"parts": [{"type": "TEXT", "content": "Why did churn increase?"}]},
    )
    assert response.status_code == 202
    assert set(response.json()) == {"message_id", "run_id", "mission_id"}
    assert runtime.run_queue.run_ids == [response.json()["run_id"]]
    persisted_run = repositories.runs.get(response.json()["run_id"])
    assert persisted_run.metadata["submission"]["query"] == "Why did churn increase?"
    assert persisted_run.metadata["submission"]["assistant_message_id"]
    messages = client.get(f"/v1/threads/{thread_id}/messages").json()
    assert len(messages) == 2
    assert messages[-1]["role"] == "ASSISTANT"
    assert messages[-1]["parts"][0]["type"] == "AGENT_STATUS"
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
    client, _, _ = _client(monkeypatch)
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
    client, _, _ = _client(monkeypatch)
    thread_id = client.post("/v1/threads", json={}).json()["id"]
    client.post(f"/v1/threads/{thread_id}/archive")
    response = client.post(
        f"/v1/threads/{thread_id}/messages",
        json={"parts": [{"type": "TEXT", "content": "Too late"}]},
    )
    assert response.status_code == 409


def test_soft_delete_restore_and_cursor_pages(monkeypatch):
    client, _, _ = _client(monkeypatch)
    ids = [
        client.post("/v1/threads", json={"title": f"Thread {index}"}).json()["id"]
        for index in range(3)
    ]
    first = client.get("/v1/threads/paginated", params={"limit": 2})
    assert first.status_code == 200
    assert len(first.json()["items"]) == 2
    assert first.json()["has_more"] is True
    assert first.headers["X-Next-Cursor"] == first.json()["next_cursor"]
    second = client.get(
        "/v1/threads/paginated",
        params={"limit": 2, "cursor": first.json()["next_cursor"]},
    )
    assert len(second.json()["items"]) == 1

    deleted = client.delete(f"/v1/threads/{ids[0]}")
    assert deleted.json()["status"] == "DELETED"
    assert client.get(f"/v1/threads/{ids[0]}").status_code == 404
    restored = client.post(f"/v1/threads/{ids[0]}/restore")
    assert restored.json()["status"] == "ACTIVE"


def test_submission_validates_and_associates_ready_attachments(monkeypatch):
    from seleric_swarm.conversations import (
        Attachment,
        AttachmentScanStatus,
        AttachmentStatus,
    )

    client, repositories, _ = _client(monkeypatch)
    thread_id = client.post("/v1/threads", json={}).json()["id"]
    ready = repositories.attachments.create(
        Attachment(
            thread_id=thread_id,
            workspace_id="workspace_1",
            owner_user_id="user_1",
            filename="data.csv",
            content_type="text/csv",
            size_bytes=4,
            storage_uri="file:///data.csv",
            checksum_sha256="a" * 64,
            status=AttachmentStatus.READY,
            scan_status=AttachmentScanStatus.CLEAN,
        )
    )
    pending = repositories.attachments.create(
        ready.model_copy(update={"id": "attachment_pending", "status": AttachmentStatus.PENDING})
    )
    rejected = client.post(
        f"/v1/threads/{thread_id}/messages",
        json={
            "parts": [{"type": "TEXT", "content": "Analyze this"}],
            "attachment_ids": [pending.id],
        },
    )
    assert rejected.status_code == 409

    accepted = client.post(
        f"/v1/threads/{thread_id}/messages",
        json={
            "parts": [{"type": "TEXT", "content": "Analyze this"}],
            "attachment_ids": [ready.id],
        },
    )
    assert accepted.status_code == 202
    assert repositories.attachments.get(ready.id).message_id == accepted.json()["message_id"]

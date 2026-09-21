from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.conversations.context import (
    ContextBuilder,
    MemoryCandidateExtractor,
    MemoryService,
    ThreadSummaryService,
    approximate_token_count,
)
from seleric_swarm.conversations.contracts import (
    Artifact,
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Message,
    MessagePart,
    MessageRole,
    Thread,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.persistence.memory import InMemoryMissionStore


def _message(thread: Thread, text: str, *, message_id: str) -> Message:
    return Message(
        id=message_id,
        thread_id=thread.id,
        workspace_id=thread.workspace_id,
        user_id=thread.owner_user_id,
        role=MessageRole.USER,
        parts=[MessagePart(type="TEXT", content=text)],
    )


def test_summary_is_verbatim_and_provenance_linked():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    messages = [
        _message(thread, "Exact first statement.", message_id="m1"),
        _message(thread, "Exact second statement.", message_id="m2"),
    ]
    summary = ThreadSummaryService(repositories).create(thread, messages)
    assert summary.summary == "Exact first statement.\nExact second statement."
    assert summary.covered_message_ids == ["m1", "m2"]
    assert all(line in {m.parts[0].content for m in messages} for line in summary.summary.splitlines())


def test_candidate_dedupe_conflict_confirmation_and_supersession():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    extractor = MemoryCandidateExtractor()
    service = MemoryService(repositories)
    first = service.ingest_candidates(
        extractor.extract(_message(thread, "I prefer dark mode.", message_id="m1"), thread)
    )
    assert len(first) == 1
    assert first[0].status is MemoryStatus.PENDING_CONSENT
    service.confirm(first[0])
    assert service.ingest_candidates(
        extractor.extract(_message(thread, "I prefer dark mode.", message_id="m2"), thread)
    ) == []
    conflict = service.ingest_candidates(
        extractor.extract(_message(thread, "I prefer not dark mode.", message_id="m3"), thread)
    )[0]
    assert conflict.supersedes_id == first[0].id
    confirmed = service.confirm(conflict)
    prior = repositories.memories.get(first[0].id, "w", "u")
    assert confirmed.status is MemoryStatus.ACTIVE
    assert prior and prior.status is MemoryStatus.SUPERSEDED
    assert prior.superseded_by_id == confirmed.id


def test_context_budgets_consent_permissions_and_usage_provenance():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(
        Thread(workspace_id="w", owner_user_id="u", project_id="p")
    )
    repositories.messages.create(_message(thread, "Question about dark mode", message_id="m1"))
    repositories.memories.create(
        MemoryItem(
            id="allowed",
            workspace_id="w",
            owner_user_id="u",
            project_id="p",
            scope=MemoryScope.PROJECT,
            type=MemoryType.PREFERENCE,
            status=MemoryStatus.ACTIVE,
            content="dark mode",
            normalized_content="dark mode",
            consented_at=datetime.now(UTC),
        )
    )
    repositories.memories.create(
        MemoryItem(
            id="unconsented",
            workspace_id="w",
            owner_user_id="u",
            project_id="p",
            scope=MemoryScope.PROJECT,
            type=MemoryType.FACT,
            content="must never be used",
            normalized_content="must never be used",
        )
    )
    repositories.memories.create(
        MemoryItem(
            id="other-owner",
            workspace_id="w",
            owner_user_id="other",
            project_id="p",
            scope=MemoryScope.PROJECT,
            type=MemoryType.FACT,
            status=MemoryStatus.ACTIVE,
            content="private",
            normalized_content="private",
            consented_at=datetime.now(UTC),
        )
    )
    repositories.artifacts.put(
        Artifact(
            id="a1",
            workspace_id="w",
            thread_id=thread.id,
            artifact_type="ui",
            payload={"x": 1},
            classification="ui",
        )
    )
    bundle = ContextBuilder(
        repositories,
        total_characters=120,
        message_characters=50,
        memory_characters=30,
        artifact_characters=20,
    ).build(thread, query="dark mode")
    assert bundle.character_count <= 120
    assert bundle.memory_ids == ["allowed"]
    assert "unconsented" not in bundle.memory_ids
    assert "other-owner" not in bundle.memory_ids
    repositories.memories.record_usage("run1", bundle.memories, {"reason": "context"})
    assert [m.id for m in repositories.memories.list_used_by_run("run1", "w", "u")] == ["allowed"]
    assert repositories.memories.list_used_by_run("run1", "w", "other") == []


def test_opt_out_and_soft_delete():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    preference = repositories.memories.get_preference("w", "u").model_copy(
        update={"opted_out": True}
    )
    repositories.memories.set_preference(preference)
    candidates = MemoryCandidateExtractor().extract(
        _message(thread, "I prefer short answers.", message_id="m1"), thread
    )
    assert MemoryService(repositories).ingest_candidates(candidates) == []
    item = repositories.memories.create(
        MemoryItem(
            workspace_id="w",
            owner_user_id="u",
            scope=MemoryScope.USER,
            type=MemoryType.FACT,
            content="x",
            normalized_content="x",
        )
    )
    assert repositories.memories.delete(item.id, "w", "u")
    assert repositories.memories.get(item.id, "w", "u") is None


def test_project_and_episodic_isolation_hard_token_limit_and_revocation_hook():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(
        Thread(workspace_id="w", owner_user_id="u", project_id="project-a")
    )
    consented_at = datetime.now(UTC)
    for memory_id, scope, project_id, content in (
        ("project-a", MemoryScope.PROJECT, "project-a", "matching project"),
        ("project-b", MemoryScope.PROJECT, "project-b", "other project secret"),
        ("episode-b", MemoryScope.EPISODIC, "project-b", "other episode secret"),
    ):
        repositories.memories.create(MemoryItem(
            id=memory_id, workspace_id="w", owner_user_id="u", project_id=project_id,
            scope=scope, type=MemoryType.FACT, status=MemoryStatus.ACTIVE,
            content=content, normalized_content=content, consented_at=consented_at,
        ))
    bundle = ContextBuilder(
        repositories, total_characters=200, total_tokens=20, memory_characters=100
    ).build(thread, query="project", permissions={"read_memory": True})
    assert bundle.memory_ids == ["project-a"]
    assert bundle.permissions == {"read_memory": True}
    assert bundle.token_estimate <= bundle.token_budget == 20
    assert approximate_token_count("dependency-free tokenizer!") >= 6

    revoked: list[str] = []
    item = MemoryService(repositories).revoke(
        "project-a", "w", "u", reason="user request",
        on_revoked=lambda memory: revoked.append(memory.id),
    )
    assert item and item.status is MemoryStatus.ARCHIVED
    assert revoked == ["project-a"]


def test_memory_api_denies_cross_tenant_and_supports_controls():
    repositories = build_in_memory_repositories()
    runtime = SimpleNamespace(conversations=repositories)
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(conversations_api.router)
    app.add_middleware(
        ApiSecurityMiddleware,
        api_key="secret",
        rate_limit_enabled=False,
        default_workspace_id="w",
        default_user_id="u",
        trust_identity_headers=True,
    )
    client = TestClient(app)

    def headers(user: str) -> dict[str, str]:
        return {
            "X-API-Key": "secret",
            "X-Workspace-ID": "w",
            "X-User-ID": user,
        }

    created = client.post(
        "/v1/memories",
        json={"content": "Keep answers short", "scope": "USER", "type": "PREFERENCE"},
        headers=headers("u"),
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]
    assert created.json()["consented_at"]
    assert client.get(f"/v1/memories/{memory_id}", headers=headers("other")).status_code == 404
    assert client.post(f"/v1/memories/{memory_id}/pin", headers=headers("u")).json()["pinned"]
    assert client.delete(f"/v1/memories/{memory_id}", headers=headers("u")).status_code == 200
    assert client.get(f"/v1/memories/{memory_id}", headers=headers("u")).status_code == 404


def test_submission_persists_context_bundle_without_changing_query(monkeypatch):
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    repositories.memories.create(
        MemoryItem(
            workspace_id="w",
            owner_user_id="u",
            scope=MemoryScope.USER,
            type=MemoryType.PREFERENCE,
            status=MemoryStatus.ACTIVE,
            content="Use concise answers",
            normalized_content="use concise answers",
            consented_at=datetime.now(UTC),
        )
    )
    store = InMemoryMissionStore()
    runtime = SimpleNamespace(
        conversations=repositories,
        store=store,
        settings=SimpleNamespace(run_max_attempts=3),
        activity_events=None,
    )

    async def no_execution(*args, **kwargs):
        return None

    monkeypatch.setattr(conversations_api, "_execute_submission", no_execution)
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(conversations_api.router)
    app.add_middleware(
        ApiSecurityMiddleware,
        api_key="secret",
        rate_limit_enabled=False,
        default_workspace_id="w",
        default_user_id="u",
        trust_identity_headers=True,
    )
    response = TestClient(app).post(
        f"/v1/threads/{thread.id}/messages",
        json={"parts": [{"type": "TEXT", "content": "Original query"}]},
        headers={
            "X-API-Key": "secret",
            "X-Workspace-ID": "w",
            "X-User-ID": "u",
        },
    )
    assert response.status_code == 202
    payload = response.json()
    run = repositories.runs.get(payload["run_id"])
    raw = store.get_raw(payload["mission_id"])
    assert run and run.metadata["context_bundle"]["memory_ids"]
    assert raw and raw["query"] == "Original query"
    assert raw["input"]["query"] == "Original query"
    assert raw["input"]["context_bundle"]["memory_ids"]


def test_answer_parts_attach_lookup_sources_for_ui():
    parts = conversations_api._answer_parts(
        "gross sales: 4789.73 (2026-09-18)",
        {
            "evidence": [
                {
                    "evidence_id": "EV-1",
                    "metric_or_fact": "metric.gross_sales",
                    "value": 4789.73,
                    "time_range": {"start": "2026-09-18", "end": "2026-09-18"},
                }
            ]
        },
    )
    assert parts[0].type.value == "TEXT"
    assert parts[1].type.value == "SOURCE"
    assert parts[1].content["title"] == "gross sales"
    assert parts[1].content["excerpt"] == "4789.73 for 2026-09-18"

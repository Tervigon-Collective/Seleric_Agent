from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from starlette.requests import Request

from seleric_swarm.api.phase7 import _admin
from seleric_swarm.conversations.contracts import (
    Artifact,
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Message,
    MessagePart,
    MessagePartType,
    MessageRole,
    Principal,
    Run,
    SearchResult,
    Thread,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.conversations.phase7 import reciprocal_rank_fusion
from seleric_swarm.observability.tracing import (
    current_trace_context,
    operation_span,
    redact_mapping,
)


def principal(user: str = "user-1", roles: set[str] | None = None) -> Principal:
    return Principal(
        principal_id=user, workspace_id="workspace-1", user_id=user,
        authenticated=True, roles=roles or set(),
    )


def test_search_is_strictly_scoped_across_all_document_kinds():
    repositories = build_in_memory_repositories()
    own = repositories.threads.create(Thread(
        workspace_id="workspace-1", owner_user_id="user-1", title="Revenue investigation"
    ))
    other = repositories.threads.create(Thread(
        workspace_id="workspace-2", owner_user_id="user-2", title="Revenue secret"
    ))
    for thread, workspace, user in (
        (own, "workspace-1", "user-1"), (other, "workspace-2", "user-2")
    ):
        message = repositories.messages.create(Message(
            thread_id=thread.id, workspace_id=workspace, user_id=user, role=MessageRole.USER,
            parts=[MessagePart(type=MessagePartType.TEXT, content="revenue anomaly")],
        ))
        run = repositories.runs.create(Run(
            thread_id=thread.id, workspace_id=workspace, requested_by_user_id=user,
            metadata={"query": "revenue"},
        ))
        repositories.artifacts.put(Artifact(
            workspace_id=workspace, artifact_type="report", payload={"title": "Revenue report"},
            thread_id=thread.id, run_id=run.id, message_id=message.id, classification="ui",
        ))
        repositories.memories.create(MemoryItem(
            workspace_id=workspace, owner_user_id=user, scope=MemoryScope.USER,
            type=MemoryType.FACT, status=MemoryStatus.ACTIVE,
            content="Revenue baseline", normalized_content="revenue baseline",
            consented_at=datetime.now(UTC),
        ))
    results = repositories.search.search("revenue", "workspace-1", "user-1", limit=50)
    assert {result.kind for result in results} == {
        "thread", "message", "memory", "artifact", "run"
    }
    assert all(result.id not in {other.id} for result in results)
    assert not repositories.search.search("secret", "workspace-1", "user-1")


def test_rrf_combines_lexical_vector_and_recency_ranks():
    now = datetime.now(UTC)
    a = SearchResult(id="a", kind="thread", title="A", created_at=now)
    b = SearchResult(id="b", kind="thread", title="B", created_at=now)
    fused = reciprocal_rank_fusion([a, b], [b], [b, a])
    assert fused[0].id == "b"
    assert fused[0].lexical_rank == 2
    assert fused[0].vector_rank == 1
    assert fused[0].recency_rank == 1








def test_span_attributes_preserve_values_and_context_propagates():
    trace.set_tracer_provider(TracerProvider())
    attributes = {
        "authorization": "Bearer abcdefghijklmnop",
        "content": "email me at private@example.com token=supersecret",
    }
    preserved = redact_mapping(attributes)
    assert preserved["authorization"] == "Bearer abcdefghijklmnop"
    assert preserved["content"] == attributes["content"]
    with operation_span("retrieval", "search", preserved):
        context = current_trace_context()
        assert len(context["trace_id"]) == 32
        with operation_span("persistence", "read"):
            assert current_trace_context()["trace_id"] == context["trace_id"]


def test_admin_endpoint_denies_non_admin_principal():
    request = Request({"type": "http", "method": "GET", "path": "/"})
    request.state.principal = principal()
    with pytest.raises(HTTPException) as caught:
        _admin(request)
    assert caught.value.status_code == 403

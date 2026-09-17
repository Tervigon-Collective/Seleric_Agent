from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from starlette.requests import Request

from seleric_swarm.api.phase7 import _admin
from seleric_swarm.conversations.contracts import (
    ApprovalRequest,
    ApprovalStatus,
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
from seleric_swarm.conversations.phase7 import (
    reciprocal_rank_fusion,
    transition_approval,
)
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
            thread_id=thread.id, run_id=run.id, message_id=message.id,
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


def test_approval_idempotency_transitions_expiry_and_write_gate():
    repository = build_in_memory_repositories().approvals
    request = ApprovalRequest(
        workspace_id="workspace-1", owner_user_id="user-1", action_type="update_bid",
        action_preview={"bid": 2.5}, required_role="approver", idempotency_key="same",
        dry_run=False, expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert repository.create(request).id == repository.create(
        request.model_copy(update={"id": "approval-other"})
    ).id
    approver = principal(roles={"approver"})
    approved = transition_approval(repository, request, ApprovalStatus.APPROVED, approver)
    with pytest.raises(PermissionError, match="disabled"):
        transition_approval(
            repository, approved, ApprovalStatus.EXECUTED, approver,
            allow_write_actions=False,
        )
    executed = transition_approval(
        repository, approved, ApprovalStatus.EXECUTED, approver,
        allow_write_actions=True,
    )
    assert executed.status is ApprovalStatus.EXECUTED
    assert [event.to_status for event in repository.list_events(request.id)] == [
        ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED
    ]

    expired = repository.create(request.model_copy(update={
        "id": "approval-expired", "idempotency_key": "expired",
        "expires_at": datetime.now(UTC) - timedelta(seconds=1),
    }))
    result = transition_approval(repository, expired, ApprovalStatus.APPROVED, approver)
    assert result.status is ApprovalStatus.EXPIRED


def test_unapproved_and_dry_run_actions_never_execute():
    repository = build_in_memory_repositories().approvals
    actor = principal(roles={"approver"})
    requested = repository.create(ApprovalRequest(
        workspace_id="workspace-1", owner_user_id="user-1", action_type="delete",
        action_preview={}, required_role="approver", idempotency_key="write",
    ))
    with pytest.raises(ValueError, match="invalid"):
        transition_approval(
            repository, requested, ApprovalStatus.EXECUTED, actor,
            allow_write_actions=True,
        )
    approved = transition_approval(repository, requested, ApprovalStatus.APPROVED, actor)
    with pytest.raises(PermissionError, match="dry-run"):
        transition_approval(
            repository, approved, ApprovalStatus.EXECUTED, actor,
            allow_write_actions=True,
        )


def test_span_attributes_are_redacted_and_context_propagates():
    trace.set_tracer_provider(TracerProvider())
    sanitized = redact_mapping({
        "authorization": "Bearer abcdefghijklmnop",
        "content": "email me at private@example.com token=supersecret",
    })
    assert sanitized["authorization"] == "[REDACTED]"
    assert "private@example.com" not in str(sanitized["content"])
    with operation_span("retrieval", "search", sanitized):
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

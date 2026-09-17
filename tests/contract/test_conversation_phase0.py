"""Phase 0 conversation contract and policy tests."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request as StarletteRequest

from seleric_swarm.api.security import ApiSecurityMiddleware, _client_key
from seleric_swarm.conversations import (
    ActivityEvent,
    Attachment,
    EventVisibility,
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Message,
    MessagePart,
    MessagePartType,
    MessageRole,
    Principal,
    PrincipalAuthMethod,
    Run,
    RunAttempt,
    Thread,
    can_view_event,
    event_for_principal,
    redact_data,
    redact_text,
)


def test_canonical_thread_run_attempt_and_attachment_contracts():
    thread = Thread(workspace_id="workspace_1", owner_user_id="user_1")
    run = Run(
        thread_id=thread.id,
        workspace_id=thread.workspace_id,
        requested_by_user_id=thread.owner_user_id,
    )
    attempt = RunAttempt(run_id=run.id, attempt_number=1)
    attachment = Attachment(
        thread_id=thread.id,
        workspace_id=thread.workspace_id,
        owner_user_id=thread.owner_user_id,
        filename="analysis.csv",
        content_type="text/csv",
        size_bytes=42,
    )
    assert attempt.run_id == run.id
    assert attachment.thread_id == thread.id
    assert {part.value for part in MessagePartType} == {
        "TEXT",
        "TABLE",
        "CODE",
        "SOURCE",
        "TOOL_CALL",
        "ARTIFACT",
        "CHART",
        "AGENT_STATUS",
        "APPROVAL",
        "WARNING",
    }

    with pytest.raises(ValidationError):
        RunAttempt(run_id=run.id, attempt_number=0)
    with pytest.raises(ValidationError):
        Attachment(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            owner_user_id=thread.owner_user_id,
            filename="bad.csv",
            content_type="text/csv",
            size_bytes=-1,
        )


def test_message_parts_validate_typed_content():
    message = Message(
        thread_id="thread_1",
        workspace_id="workspace_1",
        user_id="user_1",
        role=MessageRole.USER,
        parts=[MessagePart(type=MessagePartType.TEXT, content="Please investigate")],
    )
    assert message.parts[0].type is MessagePartType.TEXT

    with pytest.raises(ValidationError):
        MessagePart(type=MessagePartType.TEXT, content={"text": "not canonical"})

    with pytest.raises(ValidationError):
        Message(
            thread_id="thread_1",
            workspace_id="workspace_1",
            role=MessageRole.USER,
            parts=[],
        )


def test_thread_memory_requires_scope_and_explicit_consent():
    with pytest.raises(ValidationError, match="thread_id"):
        MemoryItem(
            workspace_id="workspace_1",
            owner_user_id="user_1",
            scope=MemoryScope.THREAD,
            type=MemoryType.FACT,
            content="Quarter closes in March",
        )

    with pytest.raises(ValidationError, match="consented_at"):
        MemoryItem(
            workspace_id="workspace_1",
            owner_user_id="user_1",
            scope=MemoryScope.USER,
            type=MemoryType.PREFERENCE,
            status=MemoryStatus.ACTIVE,
            content="Use tables",
        )

    memory = MemoryItem(
        workspace_id="workspace_1",
        owner_user_id="user_1",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        status=MemoryStatus.ACTIVE,
        content="Use tables",
        consented_at=datetime.now(UTC),
    )
    assert memory.status is MemoryStatus.ACTIVE


def test_redaction_covers_secrets_and_pii_inside_free_text():
    text = (
        "Email jane.doe@example.com or call +1 (415) 555-2671. "
        "Authorization: Bearer abcdefghijklmnop and api_key=topsecret123."
    )
    redacted = redact_text(text)
    assert "jane.doe@example.com" not in redacted
    assert "415" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "topsecret123" not in redacted

    payload = redact_data({"note": text, "nested": {"password": "still-secret"}})
    assert payload["nested"]["password"] == "[REDACTED]"
    assert "example.com" not in payload["note"]


def test_event_visibility_is_workspace_and_role_scoped():
    event = ActivityEvent(
        thread_id="thread_1",
        workspace_id="workspace_1",
        owner_user_id="user_1",
        event_type="tool.completed",
        visibility=EventVisibility.ADMIN,
        payload={"note": "contact ops@example.com"},
    )
    user = Principal(
        principal_id="user_1",
        workspace_id="workspace_1",
        user_id="user_1",
        authenticated=True,
        auth_method=PrincipalAuthMethod.SHARED_API_KEY,
    )
    admin = user.model_copy(update={"roles": {"admin"}})
    assert can_view_event(event, user) is False
    assert can_view_event(event, admin) is True
    assert "ops@example.com" not in event_for_principal(event, admin).payload["note"]


def test_security_middleware_attaches_authenticated_principal():
    app = FastAPI()

    @app.get("/principal")
    def principal(request: Request):
        return request.state.principal.model_dump(mode="json")

    app.add_middleware(
        ApiSecurityMiddleware,
        api_key="shared-secret",
        rate_limit_enabled=False,
        default_workspace_id="workspace_default",
        default_user_id="user_default",
        trust_identity_headers=True,
    )
    response = TestClient(app).get(
        "/principal",
        headers={
            "X-API-Key": "shared-secret",
            "X-Workspace-ID": "workspace_header",
            "X-User-ID": "user_header",
        },
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["workspace_id"] == "workspace_header"
    assert response.json()["user_id"] == "user_header"


def test_disabled_api_key_uses_default_service_principal():
    app = FastAPI()

    @app.get("/principal")
    def principal(request: Request):
        return request.state.principal.model_dump(mode="json")

    app.add_middleware(
        ApiSecurityMiddleware,
        api_key="",
        rate_limit_enabled=False,
        default_workspace_id="workspace_default",
        default_user_id="user_default",
    )
    response = TestClient(app).get("/principal")
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["auth_method"] == "SERVICE"


def test_identity_headers_cannot_self_assign_admin_without_trusted_proxy():
    app = FastAPI()

    @app.get("/principal")
    def principal(request: Request):
        return request.state.principal.model_dump(mode="json")

    app.add_middleware(
        ApiSecurityMiddleware,
        api_key="shared-secret",
        rate_limit_enabled=False,
        default_workspace_id="workspace_default",
        default_user_id="user_default",
    )
    response = TestClient(app).get(
        "/principal",
        headers={
            "X-API-Key": "shared-secret",
            "X-Workspace-ID": "attacker-workspace",
            "X-User-ID": "attacker",
            "X-Roles": "admin,internal",
        },
    )
    assert response.json()["workspace_id"] == "workspace_default"
    assert response.json()["user_id"] == "user_default"
    assert response.json()["roles"] == []


def test_forwarded_address_is_ignored_unless_explicitly_trusted():
    request = StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.10")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )
    assert _client_key(request) == "ip:127.0.0.1"
    assert _client_key(request, trust_x_forwarded_for=True) == "ip:203.0.113.10"

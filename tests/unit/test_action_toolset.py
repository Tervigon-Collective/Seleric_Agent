from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.protocols.mcp.gateway import (
    SELERIC_ACTION_CAPABILITIES,
    SELERIC_CAPABILITIES,
    MCPGateway,
    _build_allowlist,
)
from seleric_swarm.protocols.mcp.servers.seleric_remote import TOOLS
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import actions


class FakeMcpClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((agent_id, capability, arguments))
        response = self.responses[capability]
        if isinstance(response, list):
            response = response.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _ctx(mcp: FakeMcpClient) -> FakeRunContext:
    deps = SelericDeps(
        mission_id="m1",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="trace1",
        context=ContextBundle(),
        mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )
    return FakeRunContext(deps)


def _proposal() -> dict[str, Any]:
    return {
        "action_request_id": "act_1",
        "action_id": "pause_meta_ad",
        "payload": {"ad_id": "12345", "brand_id": "b1", "reason": "bad performance"},
        "eligible": True,
        "confirmation_token": "act_1.secret",
        "write_enabled": True,
        "business_rule_results": [],
    }


@pytest.mark.asyncio
async def test_proposal_hides_confirmation_token_and_uses_dedicated_identity():
    mcp = FakeMcpClient({"seleric.actions_propose": _proposal()})
    result = await actions.propose_action(_ctx(mcp), "pause_meta_ad", {"ad_id": "12345"})

    assert result.success is True
    assert "act_1" in result.summary
    assert "confirmation_token" not in result.provenance.source_metadata
    assert mcp.calls == [
        (
            "v3_agent",
            "seleric.actions_propose",
            {"action_id": "pause_meta_ad", "payload": {"ad_id": "12345"}},
        )
    ]


@pytest.mark.asyncio
async def test_validate_and_preview_use_status_and_sanitized_cached_preview():
    mcp = FakeMcpClient(
        {
            "seleric.actions_propose": _proposal(),
            "seleric.actions_status": {"action_request_id": "act_1", "status": "PROPOSED"},
        }
    )
    ctx = _ctx(mcp)
    await actions.propose_action(ctx, "pause_meta_ad", {})

    valid = await actions.validate(ctx, "act_1")
    shown = await actions.preview(ctx, "act_1")

    assert valid.success is True
    assert shown.success is True
    assert shown.provenance.source_metadata["preview"]["action_request_id"] == "act_1"
    assert "confirmation_token" not in shown.provenance.source_metadata["preview"]
    assert [call[2] for call in mcp.calls[1:]] == [
        {"action_request_id": "act_1"},
        {"action_request_id": "act_1"},
    ]


@pytest.mark.asyncio
async def test_commit_uses_hidden_token_and_idempotent_retry_polls_status():
    mcp = FakeMcpClient(
        {
            "seleric.actions_propose": _proposal(),
            "seleric.actions_commit": {
                "action_request_id": "act_1",
                "status": "EXECUTED",
                "audit_ref": "audit_1",
            },
            "seleric.actions_status": {"action_request_id": "act_1", "status": "EXECUTED"},
        }
    )
    ctx = _ctx(mcp)
    await actions.propose_action(ctx, "pause_meta_ad", {})

    first = await actions.commit_action(ctx, "act_1", "idem-1")
    second = await actions.commit_action(ctx, "act_1", "idem-1")

    assert first.success is True
    assert second.success is True
    commit_calls = [call for call in mcp.calls if call[1] == "seleric.actions_commit"]
    assert commit_calls == [
        ("v3_agent", "seleric.actions_commit", {"confirmation_token": "act_1.secret"})
    ]


@pytest.mark.asyncio
async def test_commit_without_a_live_proposal_fails_closed():
    mcp = FakeMcpClient({})
    result = await actions.commit_action(_ctx(mcp), "missing", "idem-x")
    assert result.success is False
    assert result.error_code == "ACTION_NOT_APPROVED"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_ineligible_proposal_cannot_validate_or_commit():
    proposal = _proposal()
    proposal.update({"eligible": False, "confirmation_token": None})
    mcp = FakeMcpClient(
        {
            "seleric.actions_propose": proposal,
            "seleric.actions_status": {"action_request_id": "act_1", "status": "PROPOSED"},
        }
    )
    ctx = _ctx(mcp)
    proposed = await actions.propose_action(ctx, "pause_meta_ad", {})
    valid = await actions.validate(ctx, "act_1")
    committed = await actions.commit_action(ctx, "act_1", "idem-ineligible")

    assert proposed.success is True
    assert proposed.warnings
    assert valid.error_code == "ACTION_NOT_ELIGIBLE"
    assert committed.error_code == "ACTION_NOT_APPROVED"


def test_action_tools_registered_only_on_v3_agent():
    assert {name.removeprefix("seleric.") for name in SELERIC_ACTION_CAPABILITIES} <= set(TOOLS)
    allowlist, _ = _build_allowlist()

    assert set(allowlist) == {"v3_agent"}
    assert SELERIC_ACTION_CAPABILITIES <= allowlist["v3_agent"]
    assert SELERIC_CAPABILITIES <= allowlist["v3_agent"]

    gw = object.__new__(MCPGateway)
    gw._allowlist = allowlist
    # Read capabilities remain open; writes stay v3_agent-only.
    gw._authorize("observer_agent", next(iter(SELERIC_CAPABILITIES)))
    try:
        gw._authorize("observer_agent", next(iter(SELERIC_ACTION_CAPABILITIES)))
    except PermissionError:
        pass
    else:
        raise AssertionError("legacy agent_id must not call action capabilities")


# ---- Sprint 3: confirmation-token expiry --------------------------------------------


@pytest.mark.asyncio
async def test_validate_reports_expired_proposal_as_not_eligible():
    mcp = FakeMcpClient(
        {
            "seleric.actions_propose": _proposal(),
            "seleric.actions_status": {"action_request_id": "act_1", "status": "EXPIRED"},
        }
    )
    ctx = _ctx(mcp)
    await actions.propose_action(ctx, "pause_meta_ad", {})

    result = await actions.validate(ctx, "act_1")

    assert result.success is False
    assert result.error_code == "ACTION_NOT_ELIGIBLE"
    assert result.retryable is False
    assert "EXPIRED" in result.summary


@pytest.mark.asyncio
async def test_commit_after_token_expiry_fails_closed_and_does_not_retry_forever():
    mcp = FakeMcpClient(
        {
            "seleric.actions_propose": _proposal(),
            "seleric.actions_commit": {"error": "confirmation token expired"},
            "seleric.actions_status": {"action_request_id": "act_1", "status": "FAILED"},
        }
    )
    ctx = _ctx(mcp)
    await actions.propose_action(ctx, "pause_meta_ad", {})

    first = await actions.commit_action(ctx, "act_1", "idem-expired")
    assert first.success is False
    assert first.error_code == "ACTION_COMMIT_FAILED"
    assert first.retryable is False

    # Same idempotency key again -> idempotent-retry branch (queries status),
    # never re-attempts the remote commit call with the now-dead token.
    second = await actions.commit_action(ctx, "act_1", "idem-expired")
    assert second.success is False
    commit_calls = [c for c in mcp.calls if c[1] == "seleric.actions_commit"]
    assert len(commit_calls) == 1


# ---- Sprint 3: local-state safety on failed commit ----------------------------------
#
# The frozen v3 ActionToolset contract (docs/refactor/CONTRACTS.md) has no
# rollback_action tool, and seleric-mcp exposes no rollback endpoint --
# "rollback" here means process-local confirmation-token/idempotency-key
# cleanup on commit failure, not a new remote execution-reversal capability.


@pytest.mark.asyncio
async def test_failed_commit_clears_local_confirmation_token_preventing_replay():
    mcp = FakeMcpClient(
        {
            "seleric.actions_propose": _proposal(),
            "seleric.actions_commit": {"error": "business rule violation"},
        }
    )
    ctx = _ctx(mcp)
    await actions.propose_action(ctx, "pause_meta_ad", {})

    first = await actions.commit_action(ctx, "act_1", "idem-1")
    assert first.success is False
    assert first.error_code == "ACTION_COMMIT_FAILED"

    # A distinct commit attempt (different idempotency key) must not reuse
    # the already-rejected local token -- it fails closed instead.
    second = await actions.commit_action(ctx, "act_1", "idem-2")
    assert second.success is False
    assert second.error_code == "ACTION_NOT_APPROVED"
    commit_calls = [c for c in mcp.calls if c[1] == "seleric.actions_commit"]
    assert len(commit_calls) == 1


@pytest.mark.asyncio
async def test_exception_during_commit_releases_idempotency_key_for_retry():
    mcp = FakeMcpClient(
        {
            "seleric.actions_propose": _proposal(),
            "seleric.actions_commit": [
                ConnectionError("transport down"),
                {"action_request_id": "act_1", "status": "EXECUTED", "audit_ref": "audit_2"},
            ],
        }
    )
    ctx = _ctx(mcp)
    await actions.propose_action(ctx, "pause_meta_ad", {})

    first = await actions.commit_action(ctx, "act_1", "idem-transient")
    assert first.success is False
    assert first.error_code == "MCP_UNAVAILABLE"
    assert first.retryable is True

    # Same idempotency key retried -> a transient transport failure must not
    # have permanently bound the key, so this re-attempts the real commit
    # (not the idempotent-retry/status-polling branch) and succeeds.
    second = await actions.commit_action(ctx, "act_1", "idem-transient")
    assert second.success is True
    commit_calls = [c for c in mcp.calls if c[1] == "seleric.actions_commit"]
    assert len(commit_calls) == 2

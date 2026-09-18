from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.protocols.mcp.gateway import (
    SELERIC_ACTION_CAPABILITIES,
    _build_allowlist,
)
from seleric_swarm.protocols.mcp.servers.seleric_remote import TOOLS
from seleric_swarm.registry.agent_registry import AgentRegistry
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
            return response.pop(0)
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


def test_action_tools_registered_but_not_granted_to_legacy_agents():
    assert {name.removeprefix("seleric.") for name in SELERIC_ACTION_CAPABILITIES} <= set(TOOLS)
    registry = AgentRegistry("config/agent_registry.yaml")
    allowlist, _ = _build_allowlist(registry)

    assert allowlist["v3_agent"] == SELERIC_ACTION_CAPABILITIES
    assert allowlist["observer_agent"].isdisjoint(SELERIC_ACTION_CAPABILITIES)
    assert allowlist["performance_agent"].isdisjoint(SELERIC_ACTION_CAPABILITIES)

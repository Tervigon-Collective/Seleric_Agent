"""Once live data is known to be down, data tools are withdrawn from the model.

Live bug: with MCP unconfigured the agent kept calling query_metrics for 100-400s.
The guard is enforced in code (tool availability), not left to the prompt.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import semantic


class _UnconfiguredMcp:
    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        raise NotImplementedError(f"MCP capability not available: {capability}")


def _deps() -> SelericDeps:
    return SelericDeps(
        mission_id="m-guard",
        as_of=datetime(2026, 9, 24, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t",
        run_id="r",
        trace_id="tr",
        context=ContextBundle(),
        mcp_client=_UnconfiguredMcp(),
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )






@pytest.mark.asyncio
async def test_definition_lookups_are_capped_without_raising():
    deps = _deps()

    class Ctx:
        pass

    ctx = Ctx()
    ctx.deps = deps  # type: ignore[attr-defined]
    results = [semantic._definition_budget_spent(ctx) for _ in range(semantic._MAX_DEFINITION_LOOKUPS + 2)]  # type: ignore[arg-type]

    assert all(r is None for r in results[: semantic._MAX_DEFINITION_LOOKUPS])
    spent = results[semantic._MAX_DEFINITION_LOOKUPS]
    assert spent is not None and spent.success is True
    assert "exhausted" in spent.summary


class _RowsMcp:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.calls += 1
        return {"rows": [{"net_sales": "1000"}], "provenance": {}}




class _UnknownBrandMcp:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.calls += 1
        return {"kind": "unknown", "term": arguments.get("text"), "suggestions": []}



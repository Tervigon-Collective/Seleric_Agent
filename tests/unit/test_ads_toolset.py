"""Unit tests for toolsets/ads.py (read-only ad surfaces).

Cube-backed ``query_meta_insights`` writes certified EvidenceArtifacts (same
tier as semantic.query_metrics); the live Graph/GAQL tools return reference
rows in source_metadata with an uncertified caveat and no artifact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import ads


class FakeMcpClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((capability, arguments))
        response = self.responses.get(capability)
        if isinstance(response, Exception):
            raise response
        return response


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(mcp_client: FakeMcpClient) -> SelericDeps:
    return SelericDeps(
        mission_id="mission-1",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=mcp_client,
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )


# ---- query_meta_insights (Cube-backed → certified evidence) --------------------------


@pytest.mark.asyncio
async def test_query_meta_insights_writes_evidence_per_row_and_measure():
    mcp = FakeMcpClient(
        {
            "seleric.meta_insights_query": {
                "query_id": "mi1",
                "rows": [
                    {"campaign_id": "c1", "meta_spend": "100", "meta_ctr": "0.02"},
                    {"campaign_id": "c2", "meta_spend": "250", "meta_ctr": "0.05"},
                ],
                "provenance": {"query_id": "mi1", "currency": "INR"},
                "insight_context": {"measures": ["meta_spend", "meta_ctr"]},
                "warnings": [],
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await ads.query_meta_insights(
        ctx,
        account_id="act_123",
        fields=["spend", "ctr"],
        period_start=datetime(2026, 8, 1, tzinfo=UTC),
        period_end=datetime(2026, 8, 31, tzinfo=UTC),
        level="campaign",
    )
    assert result.success is True
    # 2 rows × 2 measures = 4 certified artifacts.
    assert len(result.artifact_ids) == 4
    artifact = ctx.deps.artifact_store.get(result.artifact_ids[0])
    assert artifact.classification == "factual"
    assert artifact.payload["unit"] == "INR"
    assert artifact.payload["dimensions"]["level"] == "campaign"
    assert artifact.payload["dimensions"]["account_id"] == "act_123"
    # The gateway got the ad-shaped args (since/until time range, level).
    sent = mcp.calls[0][1]
    assert sent["level"] == "campaign"
    assert sent["time_range"] == {"since": "2026-08-01", "until": "2026-08-31"}


@pytest.mark.asyncio
async def test_query_meta_insights_no_fields_is_insufficient_evidence():
    ctx = FakeRunContext(_deps(FakeMcpClient({})))
    result = await ads.query_meta_insights(
        ctx,
        account_id="act_123",
        fields=[],
        period_start=datetime(2026, 8, 1, tzinfo=UTC),
        period_end=datetime(2026, 8, 31, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"


@pytest.mark.asyncio
async def test_query_meta_insights_error_payload_surfaces_no_artifact():
    mcp = FakeMcpClient(
        {"seleric.meta_insights_query": {"error": "Caller lacks scope 'meta_ads:read'."}}
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await ads.query_meta_insights(
        ctx,
        account_id="act_123",
        fields=["spend"],
        period_start=datetime(2026, 8, 1, tzinfo=UTC),
        period_end=datetime(2026, 8, 31, tzinfo=UTC),
    )
    assert result.success is False
    assert result.artifact_ids == []
    assert "meta_ads:read" in result.provenance.source_metadata["error"]


# ---- live reference tools (uncertified, no artifact) ---------------------------------


@pytest.mark.asyncio
async def test_list_meta_accounts_returns_reference_rows_no_artifact():
    mcp = FakeMcpClient(
        {"seleric.meta_accounts_list": {"data": [{"id": "act_1"}, {"id": "act_2"}]}}
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await ads.list_meta_accounts(ctx, brand_id="20")
    assert result.success is True
    assert result.artifact_ids == []
    assert "2 row(s)" in result.summary
    assert result.warnings == []  # accounts are discovery, no live-metric caveat


@pytest.mark.asyncio
async def test_query_google_ads_flags_live_uncertified_data():
    mcp = FakeMcpClient(
        {"seleric.google_query_gaql": {"data": [{"campaign.id": "1", "metrics.cost_micros": "5000"}]}}
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await ads.query_google_ads(
        ctx, customer_id="123-456-7890", query="SELECT campaign.id FROM campaign"
    )
    assert result.success is True
    assert result.artifact_ids == []
    assert any("uncertified" in w.lower() for w in result.warnings)


@pytest.mark.asyncio
async def test_live_tool_mcp_exception_is_retryable_unavailable():
    mcp = FakeMcpClient({"seleric.meta_accounts_list": ConnectionError("boom")})
    ctx = FakeRunContext(_deps(mcp))
    result = await ads.list_meta_accounts(ctx)
    assert result.success is False
    assert result.error_code == "MCP_UNAVAILABLE"
    assert result.retryable is True

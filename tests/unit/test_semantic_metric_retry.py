"""Unit tests for the ModelRetry pre-check on unknown metric ids.

``_reject_unknown_metric`` is validation-only: it raises ModelRetry with
candidate ids from the catalogue snapshot so the model re-picks — it never
rewrites the id to a guess (rule 1 / the no-alias-table warning in
toolsets/semantic.py). Skipped when the snapshot is empty (fail-open) or the
id is present.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai import ModelRetry

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import semantic


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _ctx(catalogue: CatalogueSnapshot) -> FakeRunContext:
    deps = SelericDeps(
        mission_id="m1",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
        catalogue=catalogue,
    )
    return FakeRunContext(deps)


def _catalogue() -> CatalogueSnapshot:
    return CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(id="net_sales", label="Net Sales", raw={"aliases": ["ns"]}),
            CatalogueMetricMeta(id="total_ad_spend", label="Ad Spend"),
        )
    )


async def test_unknown_id_with_candidates_raises_with_suggestions() -> None:
    ctx = _ctx(_catalogue())
    with pytest.raises(ModelRetry) as exc:
        await semantic._reject_unknown_metric(ctx, "net_sale")  # typo
    assert "net_sales" in str(exc.value)


async def test_known_id_does_not_raise() -> None:
    ctx = _ctx(_catalogue())
    await semantic._reject_unknown_metric(ctx, "net_sales")  # no exception


async def test_empty_catalogue_is_fail_open() -> None:
    ctx = _ctx(CatalogueSnapshot())
    await semantic._reject_unknown_metric(ctx, "anything")  # no exception (fail-open)


async def test_unrelated_id_without_close_candidates_does_not_raise() -> None:
    # No close match → don't guess; let Cube validate downstream.
    ctx = _ctx(_catalogue())
    await semantic._reject_unknown_metric(ctx, "zzzzzzz_unrelated")


class _ResolvingMcp(NullMcpClient):
    async def call(self, *, agent_id, capability, arguments):  # type: ignore[override]
        if capability == "seleric.catalogue_resolve_concept" and arguments.get("text") == "ad spend words":
            return {"kind": "resolved_concept", "metric_id": "total_ad_spend", "axes": {}}
        return {}


async def test_a_business_word_passed_as_an_id_is_pointed_at_its_catalogue_id() -> None:
    """Golden Q9 (2026-10-08): "spend" / "revenue" were reported "not modelled", and
    spend and revenue were printed as "No data available" for every campaign."""
    import dataclasses

    ctx = _ctx(_catalogue())
    ctx.deps = dataclasses.replace(ctx.deps, mcp_client=_ResolvingMcp())
    with pytest.raises(ModelRetry) as exc:
        await semantic._reject_unknown_metric(ctx, "ad_spend_words")
    assert "total_ad_spend" in str(exc.value)

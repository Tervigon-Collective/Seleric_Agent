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


def test_unknown_id_with_candidates_raises_with_suggestions() -> None:
    ctx = _ctx(_catalogue())
    with pytest.raises(ModelRetry) as exc:
        semantic._reject_unknown_metric(ctx, "net_sale")  # typo
    assert "net_sales" in str(exc.value)


def test_known_id_does_not_raise() -> None:
    ctx = _ctx(_catalogue())
    semantic._reject_unknown_metric(ctx, "net_sales")  # no exception


def test_empty_catalogue_is_fail_open() -> None:
    ctx = _ctx(CatalogueSnapshot())
    semantic._reject_unknown_metric(ctx, "anything")  # no exception (fail-open)


def test_unrelated_id_without_close_candidates_does_not_raise() -> None:
    # No close match → don't guess; let Cube validate downstream.
    ctx = _ctx(_catalogue())
    semantic._reject_unknown_metric(ctx, "zzzzzzz_unrelated")

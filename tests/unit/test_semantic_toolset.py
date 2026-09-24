"""Unit + contract tests for toolsets/semantic.py (Sprint 1, Profile B).

Contract test: the ToolResult envelope invariants from
docs/refactor/CONTRACTS.md hold regardless of which tool produced it.
Unit tests: query_metrics/drilldown/search_semantics logic against a fake
MCP client — no live network, no MetricRegistry/resolve_measure heuristic
anywhere in the call path (that's the whole point of this profile).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.agent.validation.signals import check_contradiction
from seleric_swarm.analytics.grain import validate_grain_set
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import semantic


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


def _deps(mcp_client: FakeMcpClient, *, limits: ExecutionLimits | None = None) -> SelericDeps:
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
        limits=limits or ExecutionLimits(),
    )


# ---- ToolResult envelope contract ------------------------------------------------


def test_tool_result_success_false_requires_error_code():
    with pytest.raises(ValueError, match="error_code"):
        ToolResult(success=False, summary="failed")


def test_tool_result_success_false_forbids_artifact_ids():
    with pytest.raises(ValueError, match="artifact_ids"):
        ToolResult(success=False, summary="failed", error_code="X", artifact_ids=["a1"])


def test_tool_result_success_true_is_permissive():
    result = ToolResult(success=True, summary="ok")
    assert result.artifact_ids == []


# ---- query_metrics -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_unconfigured_mcp_is_not_retryable_and_tells_the_model_to_stop():
    """Live bug: NotImplementedError ("capability not available") was marked
    retryable, so the agent kept trying other metrics for minutes."""
    mcp = FakeMcpClient(
        {"seleric.metrics_query": NotImplementedError("MCP capability not available")}
    )
    result = await semantic.query_metrics(
        FakeRunContext(_deps(mcp)),
        metric_id="total_sales",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "MCP_UNAVAILABLE"
    assert result.retryable is False
    assert "Do not retry" in result.summary


@pytest.mark.asyncio
async def test_query_metrics_writes_evidence_artifact_on_success():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"total_sales": "13638"}],
                "provenance": {"query_id": "q1", "cube_view": "commerce_orders"},
                "warnings": [],
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert len(result.artifact_ids) == 1
    artifact = ctx.deps.artifact_store.get(result.artifact_ids[0])
    assert artifact is not None
    assert artifact.payload["value"] == pytest.approx(13638.0)
    assert artifact.classification == "factual"
    assert mcp.calls[0][0] == "seleric.metrics_query"


@pytest.mark.asyncio
async def test_query_metrics_raises_model_retry_once_cube_query_budget_exhausted():
    """Real enforcement, not just a frozen field: ExecutionLimits.max_cube_queries
    (CONTRACTS.md) previously bounded nothing in this toolset -- a mission
    could issue unlimited real Cube queries. A fresh (uncached) query_metrics
    call past the limit must stop the loop via ModelRetry, not silently fetch."""
    from pydantic_ai import ModelRetry

    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"total_sales": "1"}],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp, limits=ExecutionLimits(max_cube_queries=0)))
    with pytest.raises(ModelRetry, match="Cube query budget exhausted"):
        await semantic.query_metrics(
            ctx,
            metric_id="total_sales",
            dimensions={},
            period_start=datetime(2026, 9, 17, tzinfo=UTC),
            period_end=datetime(2026, 9, 17, tzinfo=UTC),
        )
    assert mcp.calls == []  # rejected before any Cube call


@pytest.mark.asyncio
async def test_query_metrics_wildcard_dimension_value_becomes_a_breakdown():
    # Live 2026-09-22 MS3: dimensions={"product_title": "*"} meant "group by"
    # but was sent as a filter product_title="*". The "*" must become a
    # breakdown (empty value) so Cube groups instead of filtering on a literal.
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"product_return_revenue": "100", "product_title": "A"}],
                "provenance": {"query_id": "q1"},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    await semantic.query_metrics(
        ctx,
        metric_id="product_return_revenue",
        dimensions={"product_title": "*"},
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, tzinfo=UTC),
    )
    sent = mcp.calls[0][1]
    assert sent.get("dimensions") == ["product_title"]  # grouped
    assert "filters" not in sent  # not a literal filter on "*"


@pytest.mark.asyncio
async def test_query_metrics_top_n_sends_sort_and_limit():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"product_return_revenue": "100", "product_title": "A"}],
                "provenance": {"query_id": "q1"},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    await semantic.query_metrics(
        ctx,
        metric_id="product_return_revenue",
        dimensions={"product_title": ""},
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, tzinfo=UTC),
        order="desc",
        limit=10,
    )
    sent = mcp.calls[0][1]
    assert sent["sort"] == [{"field": "product_return_revenue", "direction": "desc"}]
    assert sent["limit"] == 10


@pytest.mark.asyncio
async def test_query_metrics_rejects_incompatible_dimension_with_redirect():
    # refunded_orders (order-grain) can't break down by product_title; the guard
    # must ModelRetry and name the product-grain metric that can.
    import dataclasses

    from pydantic_ai import ModelRetry

    from seleric_swarm.services.catalogue_bootstrap import (
        CatalogueMetricMeta,
        CatalogueSnapshot,
    )

    mcp = FakeMcpClient({})
    snapshot = CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(id="refunded_orders", supported_dimensions=["brand_id", "event_date"]),
            CatalogueMetricMeta(
                id="product_return_revenue",
                supported_dimensions=["brand_id", "order_date", "product_title"],
            ),
        )
    )
    deps = dataclasses.replace(_deps(mcp), catalogue=snapshot)
    ctx = FakeRunContext(deps)
    with pytest.raises(ModelRetry) as exc:
        await semantic.query_metrics(
            ctx,
            metric_id="refunded_orders",
            dimensions={"product_title": ""},
            period_start=datetime(2026, 6, 1, tzinfo=UTC),
            period_end=datetime(2026, 6, 30, tzinfo=UTC),
        )
    msg = str(exc.value)
    assert "product_title" in msg
    assert "product_return_revenue" in msg
    assert len(mcp.calls) == 0  # rejected before any Cube call


@pytest.mark.asyncio
async def test_query_metrics_repeat_returns_stop_nudge_without_duplicating_evidence():
    # Live 2026-09-22 (MS3-0b46db4d98): a lookup re-issued an identical
    # successful query_metrics ~10x and blew its step budget without answering.
    # A repeat must reuse the same artifact and hand back a blunt stop signal.
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"total_sales": "13638"}],
                "provenance": {"query_id": "q1"},
                "warnings": [],
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    kwargs = {
        "metric_id": "total_sales",
        "dimensions": {},
        "grain": "none",
        "period_start": datetime(2026, 9, 17, tzinfo=UTC),
        "period_end": datetime(2026, 9, 17, tzinfo=UTC),
    }
    first = await semantic.query_metrics(ctx, **kwargs)
    second = await semantic.query_metrics(ctx, **kwargs)
    assert second.success is True
    assert "ALREADY FETCHED" in second.summary
    assert second.artifact_ids == first.artifact_ids  # same evidence, not a new write
    assert len(ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id)) == 1


@pytest.mark.asyncio
async def test_query_metrics_multirow_summary_carries_every_value_and_unit():
    # Live 2026-09-22 MS3-ad0fe7c8a2: a 7-day series returned only the LAST
    # row as a scalar summary, so the model couldn't see the daily values and
    # fabricated them. The tool return must carry every row's value (and the
    # currency as unit) so the model reports them verbatim.
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [
                    {"nsd.day": "2026-09-16", "nsd": "16568.26"},
                    {"nsd.day": "2026-09-17", "nsd": "-742.79"},
                    {"nsd.day": "2026-09-18", "nsd": "-4963.49"},
                ],
                "provenance": {"query_id": "q1", "currency": "INR"},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="nsd",
        dimensions={},
        grain="day",
        period_start=datetime(2026, 9, 16, tzinfo=UTC),
        period_end=datetime(2026, 9, 18, tzinfo=UTC),
    )
    assert result.success is True
    # Every value present in the summary the model reads — not just the last.
    for token in ("16568.26", "-742.79", "-4963.49"):
        assert token in result.summary
    series = result.provenance.source_metadata["series"]
    assert [s["value"] for s in series] == [16568.26, -742.79, -4963.49]
    # Currency propagated onto the evidence (unit was previously null).
    artifact = ctx.deps.artifact_store.get(result.artifact_ids[0])
    assert artifact.payload["unit"] == "INR"


@pytest.mark.asyncio
async def test_query_metrics_day_grain_writes_one_artifact_per_row():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [
                    {"total_sales.day": "2026-09-15", "total_sales": "100"},
                    {"total_sales.day": "2026-09-16", "total_sales": "150"},
                    {"total_sales.day": "2026-09-17", "total_sales": "120"},
                ],
                "provenance": {"query_id": "q1"},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={},
        grain="day",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert len(result.artifact_ids) == 3
    values = sorted(
        ctx.deps.artifact_store.get(aid).payload["value"] for aid in result.artifact_ids
    )
    assert values == [100.0, 120.0, 150.0]
    first = ctx.deps.artifact_store.get(result.artifact_ids[0])
    assert first.payload["grain"] == "day"
    assert first.payload["period_start"] == first.payload["period_end"]


@pytest.mark.asyncio
async def test_query_metrics_week_grain_keeps_each_week_its_own_window():
    # Live 2026-09-23 MS3-b45585f72d: grain=week on meta_ctr returned two
    # Cube rows keyed ``….report_date.week``. row_date only matched ``.day``,
    # so both artifacts inherited the full query window and the same series
    # label. check_contradiction then treated 0.0177 and 0.0212 as one
    # metric disagreeing with itself (>5%) and the mission failed closed
    # as INSUFFICIENT_EVIDENCE.
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q_week",
                "rows": [
                    {
                        "meta_ad_performance.report_date.week": "2026-09-07T00:00:00.000",
                        "meta_ctr": "0.0212091662603608",
                    },
                    {
                        "meta_ad_performance.report_date.week": "2026-09-14T00:00:00.000",
                        "meta_ctr": "0.017666988304220088",
                    },
                ],
                "provenance": {"query_id": "q_week"},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="meta_ctr",
        dimensions={},
        grain="week",
        period_start=datetime(2026, 9, 9, tzinfo=UTC),
        period_end=datetime(2026, 9, 22, tzinfo=UTC),
    )
    assert result.success is True
    artifacts = ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id)
    parsed = [EvidenceArtifact.model_validate(a.payload) for a in artifacts]
    assert [p.period_start.date().isoformat() for p in parsed] == ["2026-09-07", "2026-09-14"]
    assert [p.period_end.date().isoformat() for p in parsed] == ["2026-09-13", "2026-09-20"]
    assert validate_grain_set(parsed) is None
    labels = [s["label"] for s in result.provenance.source_metadata["series"]]
    assert labels == ["2026-09-07..2026-09-13", "2026-09-14..2026-09-20"]
    assert not check_contradiction(artifacts).challenges


@pytest.mark.asyncio
async def test_query_metrics_no_rows_returns_insufficient_evidence():
    mcp = FakeMcpClient({"seleric.metrics_query": {"rows": [], "provenance": {}}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert result.artifact_ids == []


@pytest.mark.asyncio
async def test_query_metrics_mcp_unavailable_is_retryable():
    # call_metrics_query() (services/mcp_query.py, reused as-is) catches the
    # client exception itself and returns an {"error": ...} dict rather than
    # raising — query_metrics() surfaces that as INSUFFICIENT_EVIDENCE with
    # retryable=True, not a raised exception.
    mcp = FakeMcpClient({"seleric.metrics_query": ConnectionError("boom")})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_query_metrics_drops_invented_brand_placeholder():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"total_sales": "13638"}],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={"brand": "some_brand"},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert "filters" not in mcp.calls[0][1]
    assert len(mcp.calls) == 1


@pytest.mark.asyncio
async def test_query_metrics_retries_unfiltered_on_unknown_brand():
    class _BrandGate:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
            del agent_id
            self.calls.append((capability, arguments))
            if arguments.get("filters"):
                return {"error": "unknown brand 'nikee'"}
            return {"query_id": "q1", "rows": [{"total_sales": "200"}], "provenance": {}}

    mcp = _BrandGate()
    ctx = FakeRunContext(_deps(mcp))  # type: ignore[arg-type]
    result = await semantic.query_metrics(
        ctx,
        metric_id="total_sales",
        dimensions={"brand_id": "nikee"},
        grain="none",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert len(mcp.calls) == 2
    assert mcp.calls[0][1]["filters"]
    assert "filters" not in mcp.calls[1][1]


@pytest.mark.asyncio
async def test_query_metrics_defaults_period_to_mission_as_of():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "query_id": "q1",
                "rows": [{"total_sales": "10"}],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(ctx, metric_id="total_sales")
    assert result.success is True
    time_range = mcp.calls[0][1]["time_range"]
    assert time_range["start"] == "2026-09-18"
    assert time_range["end"] == "2026-09-18"


# ---- search_semantics / get_metric_definition ---------------------------------------


@pytest.mark.asyncio
async def test_search_semantics_uses_glossary_search_and_slims_shortlist():
    # 10 matches back; the tool keeps only the top _SEARCH_SHORTLIST and slims
    # each to the id/name/view/dims/matched_on the model needs to pick.
    matches = [
        {
            "id": f"m{i}",
            "display_name": f"Metric {i}",
            "view": "canonical_pnl",
            "supported_dimensions": ["brand_id"],
            "matched_on": "glossary:net sales" if i == 0 else "name",
            "description": "x" * 500,  # dropped by the slimmer
        }
        for i in range(10)
    ]
    mcp = FakeMcpClient({"seleric.catalogue_search_metrics": {"matches": matches}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.search_semantics(ctx, "net sales")
    assert result.success is True
    shortlist = result.provenance.source_metadata["matches"]
    assert len(shortlist) == semantic._SEARCH_SHORTLIST
    assert shortlist[0]["id"] == "m0"
    assert "description" not in shortlist[0]  # slimmed
    assert mcp.calls[0][0] == "seleric.catalogue_search_metrics"


@pytest.mark.asyncio
async def test_search_semantics_hard_stops_over_budget_with_model_retry():
    # Live 2026-09-22 MS3-53296c1a5e: the soft "STOP SEARCHING" summary was
    # ignored 11 times until the budget tripped. Past _MAX_SEARCHES the tool
    # must hard-stop with ModelRetry, not return another success.
    from pydantic_ai import ModelRetry

    mcp = FakeMcpClient(
        {"seleric.catalogue_search_metrics": {"matches": [{"id": "product_return_revenue"}]}}
    )
    ctx = FakeRunContext(_deps(mcp))
    for _ in range(semantic._MAX_SEARCHES):
        assert (await semantic.search_semantics(ctx, "returns")).success is True
    with pytest.raises(ModelRetry) as exc:
        await semantic.search_semantics(ctx, "returns again")
    assert "SEMANTIC_RESOLUTION_LOOP" in str(exc.value)


@pytest.mark.asyncio
async def test_search_semantics_hoists_exact_id_over_vector_rank():
    # An exact metric-id query must surface that metric first even when the
    # server buries it below glossary hits (live: product_net_revenue ranked
    # 31st for its own id).
    server_matches = [
        {"id": "net_sales_all_channels", "matched_on": "glossary:revenue"},
        {"id": "commerce_net_revenue", "matched_on": "name"},
        {"id": "product_net_revenue", "display_name": "Product Net Revenue (ex-GST)", "matched_on": "name"},
    ]
    mcp = FakeMcpClient({"seleric.catalogue_search_metrics": {"matches": server_matches}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.search_semantics(ctx, "product net revenue")
    ids = [m["id"] for m in result.provenance.source_metadata["matches"]]
    assert ids[0] == "product_net_revenue"


@pytest.mark.asyncio
async def test_search_semantics_falls_back_to_local_index_when_mcp_down(monkeypatch):
    monkeypatch.setattr(
        semantic.catalogue_index, "search", lambda query, **kwargs: [{"id": "total_sales", "stale": True}]
    )
    mcp = FakeMcpClient({"seleric.catalogue_search_metrics": RuntimeError("mcp down")})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.search_semantics(ctx, "revenue")
    assert result.success is True
    assert "local index" in result.summary
    assert [m["id"] for m in result.provenance.source_metadata["matches"]] == ["total_sales"]
    assert any("stale" in w for w in result.warnings)


# ---- get_metric_definitions (batch) --------------------------------------------------


@pytest.mark.asyncio
async def test_get_metric_definitions_returns_valid_and_flags_unknown():
    mcp = FakeMcpClient(
        {
            "seleric.catalogue_get_metrics": {
                "metrics": {"net_sales_all_channels": {"id": "net_sales_all_channels"}},
                "errors": {"bogus_id": {"error": "Unknown metric 'bogus_id'"}},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.get_metric_definitions(ctx, ["net_sales_all_channels", "bogus_id"])
    assert result.success is True
    assert "net_sales_all_channels" in result.provenance.source_metadata["definitions"]
    assert any("bogus_id" in w for w in result.warnings)
    # ids passed through verbatim, no local rewriting (rule 1)
    assert mcp.calls[0] == (
        "seleric.catalogue_get_metrics",
        {"metric_ids": ["net_sales_all_channels", "bogus_id"]},
    )


@pytest.mark.asyncio
async def test_get_metric_definitions_empty_ids_is_insufficient_evidence():
    ctx = FakeRunContext(_deps(FakeMcpClient({})))
    result = await semantic.get_metric_definitions(ctx, [])
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"


# A short-lived "declared alias" overlay (``ns``/``np``/``adsp`` -> catalogue
# id via ``MetricRegistry``) briefly lived here alongside these five tests.
# Removed 2026-09-19: it collapsed
# ``test_semantic_toolset_bug_regressions.py``'s bug #2 regression guard
# (two spellings of one metric — e.g. "cac"/"metric.cac" — must each reach
# Cube unchanged, never canonicalized) because ``MetricRegistry.resolve_alias``
# matches by id-prefix candidates first, not only the declared ``aliases:``
# list, so it silently canonicalized far more than short operator shorthand.
# ``search_semantics``/``query_metrics`` take ``metric_id`` verbatim again
# (rule 1: no local heuristic resolves a metric id in this module).


@pytest.mark.asyncio
async def test_get_metric_definition_missing_metric_is_insufficient_evidence():
    mcp = FakeMcpClient({"seleric.catalogue_get_metric": {"error": "not found"}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.get_metric_definition(ctx, "nonexistent_metric")
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"


# ---- drilldown -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drilldown_writes_one_artifact_per_row():
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {"query_id": "q1", "rows": [{"total_sales": "100"}], "provenance": {}},
            "seleric.metrics_drilldown": {
                "rows": [
                    {"shipping_region": "KA", "total_sales": "60"},
                    {"shipping_region": "MH", "total_sales": "40"},
                ],
                "provenance": {},
            },
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.drilldown(
        ctx,
        metric_id="total_sales",
        dimension="shipping_region",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is True
    assert len(result.artifact_ids) == 2
    drilldown_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_drilldown")
    assert drilldown_call[1]["parent_query_id"] == "q1"
    assert drilldown_call[1]["target_dimensions"] == ["shipping_region"]


@pytest.mark.asyncio
async def test_drilldown_parent_query_failure_is_insufficient_evidence():
    mcp = FakeMcpClient({"seleric.metrics_query": {"error": "boom", "rows": []}})
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.drilldown(
        ctx,
        metric_id="total_sales",
        dimension="shipping_region",
        period_start=datetime(2026, 9, 17, tzinfo=UTC),
        period_end=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"

"""Sprint 3 (Profile B) exit-criteria regression tests: bug #8 and bug #2
against the NEW consolidated fetch path (`toolsets/semantic.py`).

Both bugs already have regression tests against the OLD swarm_v2 path:
- Bug #8: previously also covered against the OLD swarm_v2 path directly
  (`coordinator/catalogue_grounding.py::dimensions_in_query()`), now removed
  along with that heuristic.
- Bug #2: `tests/skeptic/test_skeptic_agent.py::
  test_03b_alias_spellings_of_one_metric_are_still_compared` — tests
  `MetricRegistry`/`SkepticAgent` canonicalization, still valid until
  `MetricRegistry` retires in Sprint 5.

Neither proves the new path (`toolsets/semantic.py`) is immune to the same
bug class. Profile B's own Exit Criteria 2 and 3
(docs/refactor/02_PROFILE_SEMANTIC_MCP.md) require that proof — this file
is it. Fast unit-level tests only (no live network); the live cross-path
parity check is exit-criterion 1, covered by the gated characterization
suite.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
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


class ByMeasureMcpClient:
    """Routes seleric.metrics_query by the requested `measures[0]` — needed
    to prove two id spellings each reach Cube with their own literal value,
    not a canonicalized/collapsed one."""

    def __init__(self, rows_by_measure: dict[str, dict[str, Any]]) -> None:
        self.rows_by_measure = rows_by_measure
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((capability, arguments))
        measure = arguments["measures"][0]
        row = self.rows_by_measure[measure]
        return {"query_id": "q1", "rows": [row], "provenance": {"query_id": "q1"}}


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(mcp_client: Any) -> SelericDeps:
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


# ---- Bug #8: dimension misclassification ("get per day data" -> session_day_of_week) ----


@pytest.mark.asyncio
async def test_dimensions_are_passed_through_verbatim_no_keyword_matching():
    """No dimensions_in_query()-style rewriting exists anywhere in the new
    call path — whatever dimension key the LLM states reaches Cube exactly
    as given."""
    mcp = FakeMcpClient(
        {"seleric.metrics_query": {"rows": [{"net_sales": "100"}], "provenance": {}}}
    )
    ctx = FakeRunContext(_deps(mcp))
    await semantic.query_metrics(
        ctx,
        metric_id="net_sales",
        dimensions={"session_day_of_week": ""},
        grain="none",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )
    query_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_query")
    assert query_call[1]["dimensions"] == ["session_day_of_week"]


@pytest.mark.asyncio
async def test_unsupported_dimension_surfaces_mcp_rejection_not_silent_empty_success():
    """Bug #8's failure mode was a silent empty-evidence cascade three
    layers before Cube ever saw the query. The new path has one call site,
    so an unsupported dimension surfaces as a visible ToolResult failure."""
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "error": "dimension not supported for measure",
                "rows": [],
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="net_sales",
        dimensions={"session_day_of_week": ""},
        grain="none",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_per_day_phrasing_resolves_to_day_grain_not_a_dimension():
    """The original bug's literal repro: "get per day data" was routed to a
    session_day_of_week DIMENSION instead of a grain="day" AGGREGATION.
    Grain and dimension are now structurally separate parameters, so a
    calendar word can't leak into dimension-matching — there's no
    dimension-matching step to leak into."""
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "rows": [{"net_sales.day": "2026-09-15", "net_sales": "100"}],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    await semantic.query_metrics(
        ctx,
        metric_id="net_sales",
        dimensions={},
        grain="day",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )
    query_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_query")
    assert query_call[1]["granularity"] == "day"
    assert query_call[1].get("dimensions") is None


# ---- Live 2026-09-21: dimension value echoing its own key name ----


@pytest.mark.asyncio
async def test_dimension_value_equal_to_its_key_becomes_a_breakdown_not_a_dead_filter():
    """Live incident: ``dimensions={"product_title": "product_title"}`` built
    a Cube filter for a product literally named "product_title", which
    matched zero rows. The caller wanted a breakdown by product_title, not
    a filter — echoing the key back as the value is treated the same as
    leaving the value empty (rule 5: no keyword-matching, but this is
    structural — key == value can never be a real dimension value)."""
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "rows": [{"units_sold": "768", "product_title": "Pawveralls Suspender Boots"}],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="units_sold",
        dimensions={"product_title": "product_title"},
        grain="none",
        period_start=datetime(2026, 8, 21, tzinfo=UTC),
        period_end=datetime(2026, 9, 20, tzinfo=UTC),
    )
    query_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_query")
    assert query_call[1]["dimensions"] == ["product_title"]
    # No product_title filter (key==value is a breakdown, not a dead filter); the
    # only filter is the injected default brand.
    # no dead equals-filter on the breakdown; only "has a value" (rows without one are not reported)
    assert query_call[1].get("filters") == [
        {"dimension": "product_title", "operator": "set", "values": []},
        {"dimension": "brand_id", "operator": "equals", "values": ["20"]},
    ]
    assert result.success is True


# ---- Live 2026-09-21: query_metrics breakdown rows lost their dimension ----


@pytest.mark.asyncio
async def test_query_metrics_breakdown_attaches_each_rows_own_dimension_value():
    """Live incident: ``dimensions={"product_id": ""}`` (a breakdown request)
    produced rows whose evidence.dimensions was always ``{}`` — the request
    dict has an empty value for a breakdown key by definition, and the old
    code only copied truthy (filter) values into evidence. ~200 per-product
    counts came back indistinguishable from each other, so the mission
    re-fetched the same breakdown via drilldown() to get real labels,
    tripling the evidence volume fed back into every subsequent LLM turn."""
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "rows": [
                    {"product_orders": "216", "product_id": "8240181837913"},
                    {"product_orders": "108", "product_id": "8123760607321"},
                ],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="product_orders",
        dimensions={"product_id": ""},
        grain="none",
        period_start=datetime(2026, 8, 21, tzinfo=UTC),
        period_end=datetime(2026, 9, 20, tzinfo=UTC),
    )
    assert result.success is True
    payloads = [ctx.deps.artifact_store.get(aid).payload for aid in result.artifact_ids]
    dims_seen = {p["dimensions"].get("product_id") for p in payloads}
    assert dims_seen == {"8240181837913", "8123760607321"}


# ---- Live MS3-99ad433e18: a time-axis breakdown defeated grain=month ----


@pytest.mark.asyncio
async def test_time_dimension_breakdown_folds_into_grain_not_a_raw_groupby():
    """Live incident: ``dimensions={"refund_date": ""}`` + ``grain="month"`` sent
    the raw date column to Cube as a group-by, which overrode the granularity and
    returned one row per day (146 rows) — the model then hand-summed them into
    wrong monthly totals. A catalogue is_time dimension is the time axis: it must
    drive `granularity`, never a categorical group-by."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    mcp = FakeMcpClient(
        {"seleric.metrics_query": {"rows": [{"refund_count.month": "2026-04-01", "refund_count": "56"}], "provenance": {}}}
    )
    from dataclasses import replace
    deps = replace(_deps(mcp), catalogue=CatalogueSnapshot(
        metrics=(CatalogueMetricMeta(id="refund_count", view="refund_events",
                                     supported_dimensions=["refund_date"]),),
        dimensions=("refund_date",),
        time_dimensions=frozenset({"refund_date"}),
    ))
    ctx = FakeRunContext(deps)
    await semantic.query_metrics(
        ctx,
        metric_id="refund_count",
        dimensions={"refund_date": ""},
        grain="month",
        period_start=datetime(2026, 3, 29, tzinfo=UTC),
        period_end=datetime(2026, 9, 29, tzinfo=UTC),
    )
    query_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_query")
    assert query_call[1]["granularity"] == "month"
    # the raw date dimension must NOT be sent as a group-by
    assert "refund_date" not in (query_call[1].get("dimensions") or [])


@pytest.mark.asyncio
async def test_time_dimension_breakdown_with_no_grain_defaults_to_day():
    """``dimensions={"refund_date": ""}`` with no grain means "a series over
    time" — resolve it to grain=day, not a raw date group-by (which has no
    granularity suffix and can't be date-labelled)."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    mcp = FakeMcpClient(
        {"seleric.metrics_query": {"rows": [{"refund_count.day": "2026-04-01", "refund_count": "1"}], "provenance": {}}}
    )
    from dataclasses import replace
    deps = replace(_deps(mcp), catalogue=CatalogueSnapshot(
        metrics=(CatalogueMetricMeta(id="refund_count", view="refund_events",
                                     supported_dimensions=["refund_date"]),),
        time_dimensions=frozenset({"refund_date"}),
    ))
    ctx = FakeRunContext(deps)
    await semantic.query_metrics(
        ctx,
        metric_id="refund_count",
        dimensions={"refund_date": ""},
        grain="none",
        period_start=datetime(2026, 4, 1, tzinfo=UTC),
        period_end=datetime(2026, 4, 30, tzinfo=UTC),
    )
    query_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_query")
    assert query_call[1]["granularity"] == "day"
    assert "refund_date" not in (query_call[1].get("dimensions") or [])


@pytest.mark.asyncio
async def test_drilldown_routes_to_a_supporting_metric_instead_of_a_doomed_drill():
    """Live MS3-99ad433e18 part 2: drilling refund_count (view refund_events) by
    product_title returned "no rows" and the model deferred. The dimension isn't
    on the metric's view — route to a metric whose view supports it, before any
    query, naming the redirect."""
    from dataclasses import replace
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    mcp = FakeMcpClient({"seleric.metrics_query": {"query_id": "q1", "rows": [{"refund_count": "5"}]}})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(
                CatalogueMetricMeta(id="refund_count", view="refund_events",
                                    supported_dimensions=["refund_date", "order_status"]),
                CatalogueMetricMeta(id="product_refund_count", view="return_lifecycle",
                                    supported_dimensions=["product_title", "sku"]),
            ),
        ),
    )
    ctx = FakeRunContext(deps)
    result = await semantic.drilldown(
        ctx,
        metric_id="refund_count",
        dimension="product_title",
        period_start=datetime(2026, 7, 1, tzinfo=UTC),
        period_end=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert result.success is False
    assert result.error_code == "UNSUPPORTED_QUERY"
    assert "product_refund_count" in result.summary
    # it must NOT have fired the parent query for a drill it knew would fail
    assert not any(c[0] == "seleric.metrics_query" for c in mcp.calls)


@pytest.mark.asyncio
async def test_breakdown_plus_grain_summary_keeps_the_category_label():
    """Live MS3-848d29f41a: returned_units by product_title at month grain labelled
    every summary row by the month alone, dropping product_title — the model then
    reported "product with 119 returned units" with no name. The summary the model
    reads must carry the category value, not just the time bucket."""
    mcp = FakeMcpClient(
        {
            "seleric.metrics_query": {
                "rows": [
                    {"returned_units.month": "2026-06-01", "product_title": "Pawveralls Suspender Boots", "returned_units": "119"},
                    {"returned_units.month": "2026-06-01", "product_title": "WildTrail Boots", "returned_units": "8"},
                ],
                "provenance": {},
            }
        }
    )
    ctx = FakeRunContext(_deps(mcp))
    result = await semantic.query_metrics(
        ctx,
        metric_id="returned_units",
        dimensions={"product_title": ""},
        grain="month",
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, tzinfo=UTC),
    )
    assert result.success is True
    assert "Pawveralls Suspender Boots" in result.summary
    assert "119" in result.summary
    # the month bucket is still present alongside the category
    assert "2026-06" in result.summary


@pytest.mark.asyncio
async def test_large_unranked_breakdown_is_ranked_not_dumped():
    """Live MS3-848d29f41a: returned_units by product_title x month came back as 817
    rows the model then hand-ranked (dropped a month, named no product). It was then
    refused outright (2026-10-05: no data at all). Now the entities are ranked server-side
    and only the top K are returned, each with every bucket — nothing left to hand-rank."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    rows = [
        {"returned_units.month": "2026-06-01", "product_title": f"Product {i}", "returned_units": str(i)}
        for i in range(semantic._MAX_SERIES_IN_SUMMARY + 5)
    ]
    mcp = FakeMcpClient({"seleric.metrics_query": {"rows": rows, "provenance": {}}})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(CatalogueMetricMeta(id="returned_units", view="product_performance",
                                         supported_dimensions=["product_title", "order_date"]),),
            time_dimensions=frozenset({"order_date"}),
        ),
    )
    ctx = FakeRunContext(deps)
    result = await semantic.query_metrics(
        ctx,
        metric_id="returned_units",
        dimensions={"product_title": ""},
        grain="month",
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, tzinfo=UTC),
    )
    assert result.success is True, result.summary
    assert len(result.artifact_ids) == semantic._TOP_K_SERIES
    assert f"top {semantic._TOP_K_SERIES}" in result.summary and "ranked server-side" in result.summary
    rank = mcp.calls[-1][1]
    assert rank["sort"] == [{"field": "returned_units", "direction": "desc"}] and "granularity" not in rank


@pytest.mark.asyncio
async def test_per_group_topn_via_global_limit_is_blocked():
    """Live MS3-a50cf03a1a: 'most returned product per month' issued as breakdown x
    month grain + limit=5. Cube's limit is global, so it returned the 5 biggest cells
    overall and silently dropped May/June. A categorical breakdown crossed with a grain
    AND a limit must be refused and steered to a per-bucket fan-out."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    rows = [
        {"refund_lines.month": "2026-08-01", "product_title": f"Product {i}", "refund_lines": str(i)}
        for i in range(5)
    ]
    mcp = FakeMcpClient({"seleric.metrics_query": {"rows": rows, "provenance": {}}})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(CatalogueMetricMeta(id="refund_lines", view="return_lifecycle",
                                         supported_dimensions=["product_title", "refund_date"]),),
            time_dimensions=frozenset({"refund_date"}),
        ),
    )
    ctx = FakeRunContext(deps)
    result = await semantic.query_metrics(
        ctx,
        metric_id="refund_lines",
        dimensions={"product_title": ""},
        grain="month",
        period_start=datetime(2026, 5, 29, tzinfo=UTC),
        period_end=datetime(2026, 9, 29, tzinfo=UTC),
        order="desc",
        limit=5,
    )
    # graceful failed result (never a raised ModelRetry that could exhaust into a crash)
    assert result.success is False
    assert "per month" in result.summary  # names the bucket, steers to per-bucket
    assert not ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id)


@pytest.mark.asyncio
async def test_ranked_breakdown_is_allowed():
    """The same breakdown WITH order+limit is the correct shape — never blocked."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    rows = [
        {"product_title": f"Product {i}", "returned_units": str(i)}
        for i in range(semantic._MAX_SERIES_IN_SUMMARY + 5)
    ]
    mcp = FakeMcpClient({"seleric.metrics_query": {"rows": rows, "provenance": {}}})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(CatalogueMetricMeta(id="returned_units", view="product_performance",
                                         supported_dimensions=["product_title", "order_date"]),),
            time_dimensions=frozenset({"order_date"}),
        ),
    )
    ctx = FakeRunContext(deps)
    result = await semantic.query_metrics(
        ctx,
        metric_id="returned_units",
        dimensions={"product_title": ""},
        grain="none",
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        period_end=datetime(2026, 6, 30, tzinfo=UTC),
        order="desc",
        limit=5,
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_long_pure_time_series_is_not_blocked():
    """A grain-only time series is legitimately long (365 daily rows) and has no
    categorical breakdown — the guard must key on the non-time breakdown, not raw count."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    rows = [
        {"net_sales.day": f"2026-{(i % 12) + 1:02d}-01", "net_sales": str(i)}
        for i in range(semantic._MAX_SERIES_IN_SUMMARY + 5)
    ]
    mcp = FakeMcpClient({"seleric.metrics_query": {"rows": rows, "provenance": {}}})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(CatalogueMetricMeta(id="net_sales", view="canonical_pnl",
                                         supported_dimensions=["report_date"]),),
            time_dimensions=frozenset({"report_date"}),
        ),
    )
    ctx = FakeRunContext(deps)
    result = await semantic.query_metrics(
        ctx,
        metric_id="net_sales",
        dimensions={},
        grain="day",
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 12, 31, tzinfo=UTC),
    )
    assert result.success is True


# ---- Bug #2: metric-ID canonicalization inconsistency ----


@pytest.mark.asyncio
async def test_two_spellings_of_same_metric_each_produce_their_own_artifact_no_silent_remap():
    """The old bug: lookup_fast_path.py::_canon() called MetricRegistry.get()
    directly, which could return a different id depending on catalogue
    warmth, so two spellings of the same metric silently diverged or
    collapsed downstream. The new path has no canonicalization step:
    metric_id is used as the literal Cube `measures` value, and each
    artifact records exactly the id it was fetched with."""
    mcp = ByMeasureMcpClient(
        {
            "cac": {"cac": "1500"},
            "metric.cac": {"metric.cac": "1500"},
        }
    )
    ctx = FakeRunContext(_deps(mcp))

    result_a = await semantic.query_metrics(
        ctx,
        metric_id="cac",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )
    result_b = await semantic.query_metrics(
        ctx,
        metric_id="metric.cac",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 15, tzinfo=UTC),
        period_end=datetime(2026, 9, 15, tzinfo=UTC),
    )

    assert result_a.success is True
    assert result_b.success is True
    measures_sent = [c[1]["measures"][0] for c in mcp.calls if c[0] == "seleric.metrics_query"]
    assert measures_sent == ["cac", "metric.cac"]

    artifact_a = ctx.deps.artifact_store.get(result_a.artifact_ids[0])
    artifact_b = ctx.deps.artifact_store.get(result_b.artifact_ids[0])
    assert artifact_a.payload["metric_id"] == "cac"
    assert artifact_b.payload["metric_id"] == "metric.cac"


def test_metric_id_never_resolved_through_metric_registry():
    """Structural guard against reintroducing bug #2's dependency: the
    semantic toolset module must never reference MetricRegistry."""
    assert "MetricRegistry" not in dir(semantic)
    assert not any("MetricRegistry" in str(getattr(semantic, name)) for name in dir(semantic))


# ---- Bound concept filters, list-dimension breakdown, and time-crossed guidance ----


@pytest.mark.asyncio
async def test_resolve_concept_binds_filter_and_propagates_to_query_metrics():
    """When resolve_concept resolves a metric with a bound filter, that filter
    must be advertised in the tool summary, stored in the deps query cache,
    and automatically applied to query_metrics if not explicitly overridden."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    mcp = FakeMcpClient({
        "seleric.catalogue_resolve_concept": {
            "kind": "resolved_concept",
            "metric_id": "test_metric_id",
            "axes": {"scope": "ads"},
            "filter": {"platform_dim": "test_platform"},
        },
        "seleric.metrics_query": {
            "rows": [{"test_metric_id": "100"}],
            "provenance": {"query_id": "q1"},
        },
    })
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(CatalogueMetricMeta(id="test_metric_id", view="test_view",
                                         supported_dimensions=["platform_dim"]),),
        ),
    )
    ctx = FakeRunContext(deps)

    res_concept = await semantic.resolve_concept(ctx, concept="concept_query")
    assert res_concept.success is True
    assert "platform_dim" in res_concept.summary
    assert deps.query_cache.peek("concept_filter:test_metric_id") == {"platform_dim": "test_platform"}

    res_query = await semantic.query_metrics(
        ctx,
        metric_id="test_metric_id",
        dimensions={},
        grain="none",
        period_start=datetime(2026, 9, 1, tzinfo=UTC),
        period_end=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert res_query.success is True
    query_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_query")
    filters_sent = query_call[1].get("filters") or []
    assert any(f.get("dimension") == "platform_dim" and f.get("values") == ["test_platform"] for f in filters_sent)


@pytest.mark.asyncio
async def test_query_metrics_list_dimension_becomes_breakdown_and_clean_row_dimensions():
    """When a dimension is passed as a list of values (e.g. comparing entities)
    without an explicit breakdown:
    1. It is automatically added to breakdown so Cube groups by it.
    2. Evidence rows receive the row's specific dimension value, NOT a comma-joined list."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    rows = [
        {"test_metric.day": "2026-09-01", "entity_dim": "EntityA", "test_metric": "50"},
        {"test_metric.day": "2026-09-01", "entity_dim": "EntityB", "test_metric": "70"},
    ]
    mcp = FakeMcpClient({"seleric.metrics_query": {"rows": rows, "provenance": {}}})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(CatalogueMetricMeta(id="test_metric", view="test_view",
                                         supported_dimensions=["entity_dim", "order_date"]),),
            time_dimensions=frozenset({"order_date"}),
        ),
    )
    ctx = FakeRunContext(deps)

    res = await semantic.query_metrics(
        ctx,
        metric_id="test_metric",
        dimensions={"entity_dim": ["EntityA", "EntityB"]},
        grain="day",
        period_start=datetime(2026, 9, 1, tzinfo=UTC),
        period_end=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert res.success is True
    query_call = next(c for c in mcp.calls if c[0] == "seleric.metrics_query")
    assert "entity_dim" in (query_call[1].get("dimensions") or [])

    art_a = ctx.deps.artifact_store.get(res.artifact_ids[0])
    art_b = ctx.deps.artifact_store.get(res.artifact_ids[1])
    assert art_a.payload["dimensions"] == {"entity_dim": "EntityA"}
    assert art_b.payload["dimensions"] == {"entity_dim": "EntityB"}


@pytest.mark.asyncio
async def test_time_crossed_breakdown_with_bounded_list_not_rejected():
    """A time-crossed query (grain != 'none') with an entity breakdown that is
    explicitly bounded to a small list of items should NOT be rejected as an unranked dump,
    even if the total row count (e.g. 3 entities x 14 days = 42 rows) exceeds _MAX_SERIES_IN_SUMMARY."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    entities = ["E1", "E2", "E3"]
    rows = [
        {"test_metric.day": f"2026-09-{d:02d}", "entity_dim": e, "test_metric": "10"}
        for d in range(1, 15)
        for e in entities
    ]
    assert len(rows) > semantic._MAX_SERIES_IN_SUMMARY

    mcp = FakeMcpClient({"seleric.metrics_query": {"rows": rows, "provenance": {}}})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(CatalogueMetricMeta(id="test_metric", view="test_view",
                                         supported_dimensions=["entity_dim", "order_date"]),),
            time_dimensions=frozenset({"order_date"}),
        ),
    )
    ctx = FakeRunContext(deps)

    res = await semantic.query_metrics(
        ctx,
        metric_id="test_metric",
        dimensions={"entity_dim": entities},
        grain="day",
        period_start=datetime(2026, 9, 1, tzinfo=UTC),
        period_end=datetime(2026, 9, 14, tzinfo=UTC),
    )
    assert res.success is True
    assert len(res.artifact_ids) == len(rows)


@pytest.mark.asyncio
async def test_time_crossed_unbounded_dump_returns_the_top_entities_with_full_series():
    """Live 2026-10-05: "CTR by campaign per day" returned 151 campaigns and the tool refused it
    (UNSUPPORTED_QUERY) although every number was there. It now ranks the entities server-side over the
    whole period (a ratio by its catalogue volume_metric) and keeps every bucket of the top K."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    n_entities, n_days = 30, 3
    rows = [
        {"ctr.day": f"2026-09-0{d}", "campaign_name": f"C{e:02d}", "ctr": "0.02"}
        for e in range(n_entities) for d in range(1, n_days + 1)
    ]
    ranked = [{"campaign_name": f"C{e:02d}", "impressions": str(1000 - e)} for e in reversed(range(n_entities))]

    class Mcp(FakeMcpClient):
        async def call(self, *, agent_id, capability, arguments):
            self.calls.append((capability, arguments))
            if arguments.get("measures", [arguments.get("measure")])[0] == "impressions" or \
                    arguments.get("measure") == "impressions":
                return {"rows": ranked[: arguments.get("limit") or len(ranked)], "provenance": {}}
            return {"rows": rows, "provenance": {}}

    mcp = Mcp({})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(
                CatalogueMetricMeta(id="ctr", view="paid_media", supported_dimensions=["campaign_name", "report_date"],
                                    raw={"aggregation": "ratio", "volume_metric": "impressions"}),
                CatalogueMetricMeta(id="impressions", view="paid_media",
                                    supported_dimensions=["campaign_name", "report_date"], raw={"aggregation": "additive"}),
            ),
            time_dimensions=frozenset({"report_date"}),
        ),
    )
    res = await semantic.query_metrics(
        FakeRunContext(deps), metric_id="ctr", dimensions={"campaign_name": ""}, grain="day",
        period_start=datetime(2026, 9, 1, tzinfo=UTC), period_end=datetime(2026, 9, 3, tzinfo=UTC),
    )
    assert res.success is True, res.summary
    k = semantic._TOP_K_SERIES
    assert len(res.artifact_ids) == k * n_days            # every day of each kept campaign
    assert f"top {k}" in res.summary and "impressions" in res.summary
    assert "C29" in res.summary and "C00" not in res.summary.split("]")[0]   # ranked by volume, desc
    rank_call = [a for c, a in mcp.calls if "impressions" in str(a.get("measures") or a.get("measure"))]
    assert rank_call and rank_call[0].get("limit") == k and not rank_call[0].get("granularity")


@pytest.mark.asyncio
async def test_unranked_large_breakdown_without_grain_is_ranked_server_side_not_refused():
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    n = semantic._MAX_SERIES_IN_SUMMARY + 10
    rows = [{"variant_title": f"V{i}", "product_net_revenue": str(i)} for i in range(n)]
    mcp = FakeMcpClient({"seleric.metrics_query": {"rows": rows, "provenance": {}}})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(
            metrics=(CatalogueMetricMeta(id="product_net_revenue", view="product",
                                         supported_dimensions=["variant_title", "order_date"],
                                         raw={"aggregation": "additive"}),),
            time_dimensions=frozenset({"order_date"}),
        ),
    )
    res = await semantic.query_metrics(
        FakeRunContext(deps), metric_id="product_net_revenue", dimensions={"variant_title": ""},
        period_start=datetime(2026, 9, 1, tzinfo=UTC), period_end=datetime(2026, 9, 30, tzinfo=UTC),
    )
    assert res.success is True, res.summary
    assert len(res.artifact_ids) == n                      # "list all variants" keeps every row
    assert mcp.calls[-1][1]["sort"] == [{"field": "product_net_revenue", "direction": "desc"}]
    assert "ranked by product_net_revenue" in res.summary


@pytest.mark.asyncio
async def test_order_grain_metric_by_product_redirects_to_its_grain_twin():
    """Live 2026-10-04: "gross sale by product" / "COGS of these products" — the redirect listed
    alphabetical metrics and the model answered with net revenue / the brand total. The catalogue's
    grain_twins name the product-grain metric; it is the one offered."""
    from pydantic_ai import ModelRetry
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    prod_dims = ["product_title", "sku", "order_date"]
    mcp = FakeMcpClient({})
    deps = replace(
        _deps(mcp),
        catalogue=CatalogueSnapshot(metrics=(
            CatalogueMetricMeta(id="gross_sales", supported_dimensions=["brand_id", "order_date"],
                                raw={"grain_twins": ["product_gross_sale"]}),
            CatalogueMetricMeta(id="event_count", supported_dimensions=prod_dims),
            CatalogueMetricMeta(id="product_net_revenue", supported_dimensions=prod_dims),
            CatalogueMetricMeta(id="product_gross_sale", supported_dimensions=prod_dims),
        )),
    )
    # 2026-10-08: the declared twin is answered directly (no ModelRetry round trip) and named in the summary
    mcp = ByMeasureMcpClient({"product_gross_sale": {"product_gross_sale": 10.0, "sku": "A"}})
    deps = replace(deps, mcp_client=mcp)
    result = await semantic.query_metrics(
        FakeRunContext(deps), metric_id="gross_sales", dimensions={"sku": ""},
        period_start=datetime(2026, 9, 1, tzinfo=UTC), period_end=datetime(2026, 9, 30, tzinfo=UTC),
    )
    assert result.success, result.summary
    assert [a["measures"] for _, a in mcp.calls] == [["product_gross_sale"]]
    assert "grain twin 'product_gross_sale'" in result.summary
    assert "event_count" not in result.summary and "product_net_revenue" not in result.summary
    assert ModelRetry  # still the path when no twin carries the slice (see the conformance tests)


def test_redirect_alternatives_rank_by_shared_id_words():
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    snap = CatalogueSnapshot(metrics=tuple(
        CatalogueMetricMeta(id=m, supported_dimensions=["product_title"])
        for m in ("event_count", "events_per_session", "orders", "product_net_cogs", "units_sold")
    ))
    assert snap.metrics_supporting_dimension("product_title", like="net_cogs")[0] == "product_net_cogs"
    assert snap.metrics_supporting_dimension("product_title")[0] == "event_count"   # no hint: alphabetical


@pytest.mark.asyncio
async def test_list_metrics_lists_the_catalogue_by_domain():
    """Live MS3-29049b3e92: "list all the metrics you can query … at what grain"
    had no tool and shipped a truncated answer."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    deps = replace(
        _deps(FakeMcpClient({})),
        catalogue=CatalogueSnapshot(
            metrics=(
                CatalogueMetricMeta(id="ctr", view="paid_media", raw={
                    "display_name": "CTR", "aggregation": "ratio", "extra_granularities": ["hour"],
                    "description": "Click-through rate. Date axis: report_date (IST); grain: ad_day."}),
                CatalogueMetricMeta(id="net_sales", view="commerce", raw={"display_name": "Net sales"}),
            )
        ),
    )
    res = await semantic.list_metrics(FakeRunContext(deps))
    assert res.success and "[paid_media]" in res.summary and "[commerce]" in res.summary
    assert "row grain: ad_day" in res.summary and "day/week/month/quarter/year/hour" in res.summary
    one = await semantic.list_metrics(FakeRunContext(deps), domain="paid")
    assert "ctr" in one.summary and "net_sales" not in one.summary
    assert not (await semantic.list_metrics(FakeRunContext(deps), domain="nope")).success

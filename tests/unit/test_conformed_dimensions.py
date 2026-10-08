"""Conformed dimensions, grain-twin routing and structured filters (2026-10-08).

Live 2026-10-07 (thread_590ab450…, "Which Meta campaigns performed best in the last 7 days? Rank them
using spend, revenue, ROAS, CAC, CTR, CPC, CPM, clicks, LPVs, and purchases"): the Meta scope went out as
ad_platform on orders / net_sales / page_views and as finance_channel on ad_spend / ctr / clicks, seven
ModelRetry bounces came back in one step, and the answer shipped with net ROAS only. The catalogue now
declares dimension families and grain twins; the tools answer through them instead of bouncing.

Fixtures use sample ids; the logic under test reads only the catalogue snapshot.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai import ModelRetry
from test_semantic_toolset_bug_regressions import FakeRunContext, _deps

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.scope import RequiredScope, ValueFilter
from seleric_swarm.agent.validation.signals import check_scope_coverage
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.toolsets import semantic

SEP = {"period_start": datetime(2026, 9, 1, tzinfo=UTC), "period_end": datetime(2026, 9, 30, tzinfo=UTC)}

PNL_DIMS = ["brand_id", "order_date", "finance_channel", "ad_platform", "campaign_name"]
SNAP = CatalogueSnapshot(
    metrics=(
        CatalogueMetricMeta(id="net_profit", supported_dimensions=PNL_DIMS, raw={"unit": "INR"}),
        CatalogueMetricMeta(id="orders", supported_dimensions=["brand_id", "order_date", "platform", "finance_channel",
                                                               "ad_platform", "campaign_name"],
                            raw={"unit": "count", "grain_twins": ["product_orders"]}),
        CatalogueMetricMeta(id="product_orders", supported_dimensions=["brand_id", "order_date", "product_title"]),
        CatalogueMetricMeta(id="cac", supported_dimensions=["brand_id", "report_date"],
                            raw={"unit": "INR", "grain_twins": ["channel_cac"]}),
        CatalogueMetricMeta(id="channel_cac", supported_dimensions=PNL_DIMS, raw={"unit": "INR"}),
        CatalogueMetricMeta(id="repeat_rate", supported_dimensions=["brand_id", "last_order_at", "acquisition_platform"]),
        CatalogueMetricMeta(id="ad_spend", supported_dimensions=["brand_id", "report_date", "ad_platform",
                                                                 "campaign_name"], raw={"unit": "INR"}),
        CatalogueMetricMeta(id="sessions", supported_dimensions=["brand_id", "session_date", "device_type"]),
    ),
    time_dimensions=frozenset({"order_date", "report_date", "session_date", "last_order_at"}),
    dimension_families=(("platform", "platform", 0), ("finance_channel", "platform", 1), ("ad_platform", "platform", 2),
                        ("acquisition_platform", "platform", 3), ("campaign_name", "campaign", 0)),
    dimension_values=(("finance_channel", ("meta", "google", "whatsapp", "organic", "unattributed")),
                      ("ad_platform", ("meta", "google"))),
)


class RecordingMcp:
    """Answers metrics_query with one row per call: the measure plus every requested dimension = 'meta'."""

    def __init__(self, currency: str = "INR") -> None:
        self.calls: list[dict[str, Any]] = []
        self.currency = currency

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(arguments)
        row = {arguments["measures"][0]: 1234.5678, **{d: "meta" for d in arguments.get("dimensions") or []}}
        return {"query_id": "q1", "rows": [row], "provenance": {"query_id": "q1", "currency": self.currency}}


def _ctx(mcp: RecordingMcp) -> FakeRunContext:
    return FakeRunContext(replace(_deps(mcp), catalogue=SNAP))


def _filters(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in call.get("filters") or [] if f["dimension"] != "brand_id"]


@pytest.mark.asyncio
async def test_a_dimension_the_view_lacks_is_answered_by_its_conformed_sibling():
    mcp = RecordingMcp()
    result = await semantic.query_metrics(_ctx(mcp), "net_profit", dimensions={"platform": "meta"}, **SEP)
    assert result.success, result.summary
    assert _filters(mcp.calls[-1]) == [{"dimension": "finance_channel", "operator": "equals", "values": ["meta"]}]
    assert "'platform' is answered by its conformed dimension 'finance_channel'" in result.summary


@pytest.mark.asyncio
async def test_a_sibling_never_takes_a_value_it_does_not_hold():
    # email is a traffic platform, not a P&L channel: no sibling, and the other metrics carrying `platform`
    # are named (a different concept: the model chooses)
    with pytest.raises(ModelRetry) as exc:
        await semantic.query_metrics(_ctx(RecordingMcp()), "net_profit", dimensions={"platform": "email"}, **SEP)
    assert "orders" in str(exc.value)


@pytest.mark.asyncio
async def test_customers_take_the_platform_scope_on_their_acquisition_platform():
    mcp = RecordingMcp()
    result = await semantic.query_metrics(_ctx(mcp), "repeat_rate", dimensions={"ad_platform": "meta"}, **SEP)
    assert result.success
    assert _filters(mcp.calls[-1])[0]["dimension"] == "acquisition_platform"


@pytest.mark.asyncio
async def test_a_slice_only_the_grain_twin_carries_is_answered_by_the_twin():
    mcp = RecordingMcp()
    result = await semantic.query_metrics(
        _ctx(mcp), "cac", dimensions={"ad_platform": "meta", "campaign_name": ""}, **SEP)
    assert result.success, result.summary
    assert mcp.calls[-1]["measures"] == ["channel_cac"]
    assert "grain twin 'channel_cac'" in result.summary
    # orders by product -> the product-grain twin
    result = await semantic.query_metrics(_ctx(mcp), "orders", dimensions={"product_title": ""}, **SEP)
    assert mcp.calls[-1]["measures"] == ["product_orders"] and "grain twin 'product_orders'" in result.summary


@pytest.mark.asyncio
async def test_another_views_date_dimension_is_this_metrics_time_axis():
    mcp = RecordingMcp()
    # a breakdown by another view's date is the grain (live: 'net_sales' does not support 'report_date')
    result = await semantic.query_metrics(_ctx(mcp), "net_profit", dimensions={"report_date": ""}, **SEP)
    assert result.success and mcp.calls[-1].get("granularity") == "day"
    assert "report_date" not in (mcp.calls[-1].get("dimensions") or [])
    # a date filter names the period
    await semantic.query_metrics(_ctx(mcp), "net_profit", dimensions={"report_date": "2026-09-05"})
    assert mcp.calls[-1]["time_range"] == {"start": "2026-09-05", "end": "2026-09-05"}


@pytest.mark.asyncio
async def test_structured_filters_reach_the_query_and_equals_folds_into_evidence():
    mcp = RecordingMcp()
    ctx = _ctx(mcp)
    result = await semantic.query_metrics(
        ctx, "ad_spend", dimensions={"campaign_name": ""},
        filters=[{"dimension": "campaign_name", "operator": "notContains", "values": ["TEST"]},
                 {"dimension": "ad_spend", "operator": "gt", "values": ["10000"]},
                 {"dimension": "finance_channel", "operator": "equals", "values": ["meta"]}],
        **SEP,
    )
    assert result.success, result.summary
    flt = _filters(mcp.calls[-1])
    assert {"dimension": "campaign_name", "operator": "notContains", "values": ["TEST"]} in flt
    assert {"dimension": "ad_spend", "operator": "gt", "values": ["10000"]} in flt
    # finance_channel is not on ad delivery here: its sibling ad_platform holds meta, and as an equals
    # filter it is a dimension value — on the evidence, where the scope check reads it
    assert {"dimension": "ad_platform", "operator": "equals", "values": ["meta"]} in flt
    evidence = [a for a in ctx.deps.artifact_store.list_for_mission("mission-1") if a.artifact_type == "evidence"] \
        if hasattr(ctx.deps.artifact_store, "list_for_mission") else []
    for art in evidence:
        assert EvidenceArtifact.model_validate(art.payload).dimensions.get("ad_platform") == "meta"


@pytest.mark.asyncio
async def test_summary_values_are_rounded_and_carry_the_currency_only_for_money():
    mcp = RecordingMcp()
    result = await semantic.query_metrics(_ctx(mcp), "net_profit", **SEP)
    assert result.summary.startswith("net_profit=1234.57 INR over")
    result = await semantic.query_metrics(_ctx(mcp), "sessions", **SEP)
    assert result.summary.startswith("sessions=1234.57 over")  # a count carries no currency


@pytest.mark.asyncio
async def test_drilldown_takes_filters_and_conforms_its_dimension():
    class DrillMcp(RecordingMcp):
        async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
            self.calls.append({"capability": capability, **arguments})
            if capability == "seleric.metrics_drilldown":
                return {"rows": [{"net_profit": 5.0, "finance_channel": "meta"}], "provenance": {}}
            return {"query_id": "q1", "rows": [{"net_profit": 5.0}], "provenance": {"query_id": "q1"}}

    mcp = DrillMcp()
    result = await semantic.drilldown(
        _ctx(mcp), "net_profit", "platform", SEP["period_start"], SEP["period_end"],
        filters=[{"dimension": "campaign_name", "operator": "startsWith", "values": ["TH-"]}],
    )
    assert result.success, result.summary
    parent, drill = mcp.calls[0], mcp.calls[1]
    assert {"dimension": "campaign_name", "operator": "startsWith", "values": ["TH-"]} in _filters(parent)
    assert drill["target_dimensions"] == ["finance_channel"]
    assert "conformed dimension 'finance_channel'" in result.summary


def test_carries_follows_siblings_and_twins():
    assert SNAP.carries("net_profit", "platform")        # sibling finance_channel
    assert SNAP.carries("cac", "campaign_name")           # twin channel_cac
    assert SNAP.carries("orders", "product_title")        # twin product_orders
    assert SNAP.carries("net_profit", "report_date")      # time is the grain everywhere
    assert not SNAP.carries("sessions", "product_title")  # nothing carries it


def test_scope_coverage_accepts_a_conformed_sibling():
    from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance

    ev = EvidenceArtifact(metric_id="net_profit", dimensions={"finance_channel": "meta"}, grain="none",
                          as_of=SEP["period_end"], period_start=SEP["period_start"], period_end=SEP["period_end"],
                          value=1.0, source_query={"measure": "net_profit"})
    art = Artifact(workspace_id="ws-1", artifact_type="evidence", payload=ev.model_dump(mode="json"),
                   classification="factual", evidence_ids=["raw:1"], provenance=ArtifactProvenance(query_version="q"),
                   mission_id="m1")
    scope = RequiredScope(breakdowns=frozenset({frozenset({"platform"})}),
                          value_filters=(ValueFilter("meta", frozenset({"platform"}), ("meta",)),))
    # the answer grouped by finance_channel — the conformed sibling of the asked platform — covers both
    assert check_scope_coverage([art], scope, catalogue=SNAP, cited=[art.id]).status == "OK"
    # without the family (an older snapshot) the same evidence would not cover it
    bare = replace(SNAP, dimension_families=())
    assert check_scope_coverage([art], scope, catalogue=bare, cited=[art.id]).status != "OK"


@pytest.mark.asyncio
async def test_a_value_gap_is_never_answered_by_a_grain_twin():
    # net_profit's product-line twin carries the traffic `platform`, but that twin is another measure:
    # "email" not being a P&L channel is a value gap, not a grain gap
    snap = replace(SNAP, metrics=(
        *(m for m in SNAP.metrics if m.id != "net_profit"),
        CatalogueMetricMeta(id="net_profit", supported_dimensions=PNL_DIMS, raw={"grain_twins": ["product_profit"]}),
        CatalogueMetricMeta(id="product_profit", supported_dimensions=["brand_id", "order_date", "platform"]),
    ))
    mcp = RecordingMcp()
    ctx = FakeRunContext(replace(_deps(mcp), catalogue=snap))
    with pytest.raises(ModelRetry):
        await semantic.query_metrics(ctx, "net_profit", dimensions={"platform": "email"}, **SEP)
    assert not mcp.calls


@pytest.mark.asyncio
async def test_two_measures_collapsing_onto_one_id_are_reported():
    from seleric_swarm.agent.plan import MetricSlot, _resolve_metrics

    async def resolver(words: list[str]) -> dict[str, str | None]:
        return {w: "orders" for w in words}

    ids, notes = await _resolve_metrics(
        [MetricSlot(words="purchases", metric_id=""), MetricSlot(words="checkouts", metric_id="")], resolver, SNAP)
    assert ids == ["orders"]
    assert any("'checkouts' resolved to orders, like an earlier measure" in n for n in notes)

from types import SimpleNamespace

import pytest

from seleric_swarm.agents.base import AgentContext
from seleric_swarm.agents.intelligence.observer import Agent as ObserverAgent
from seleric_swarm.prompts.registry import PromptRegistry


class _FakeLLM:
    """Stands in for the dimension_map LLM call — returns the dimension(s)
    it was constructed with, grounded exactly like the real prompt (the test
    supplies what a real model would answer given the real supported_dimensions)."""

    def __init__(self, dimensions: list[str]):
        self._dimensions = dimensions

    async def complete_structured(self, request, schema):
        return SimpleNamespace(value=schema(dimensions=self._dimensions))


def _runtime_with_llm(*, metrics, mcp, dimensions: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        metrics=metrics,
        mcp=mcp,
        ontology=None,
        llm=_FakeLLM(dimensions),
        prompts=PromptRegistry("prompts", "config/prompt_versions.yaml"),
        settings=SimpleNamespace(llm_timeout_s=5.0, workflow_name="lookup_v1", workflow_version="1.0.0"),
        agents=SimpleNamespace(version=lambda agent_id, default: default),
    )


class _FakeMetrics:
    def __init__(self, definition):
        self._definition = definition

    def get(self, metric_id):
        return self._definition

    def ids_for_domain(self, domain):
        return [self._definition.id]


class _FakeGateway:
    def __init__(self, responses):
        # one response per capability, popped in call order for that capability
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict] = []
        self.capabilities = set(responses)

    async def call(self, *, agent_id, capability, arguments):
        self.calls.append({"agent_id": agent_id, "capability": capability, "arguments": arguments})
        return self._responses[capability].pop(0)


def _seleric_definition() -> SimpleNamespace:
    return SimpleNamespace(
        id="metric.net_profit",
        unit="INR",
        version=2,
        formula="net_sales_all_channels - total_operating_cost_all_channels - total_ad_spend",
        description="All-channels Net Profit (matches catalogue glossary bare 'net profit').",
        domain="finance",
        catalogue_metric="net_profit_all_channels",
        seleric_module=None,
        raw={"catalogue_metric": "net_profit_all_channels"},
    )


def _ctx(metric_id: str, day: str = "2026-09-02") -> AgentContext:
    return AgentContext(
        mission_id="M-test",
        task_id="T-1",
        question="test",
        mission_lead="finance_agent",
        payload={
            "metric_id": metric_id,
            "allowed_metrics": [metric_id],
            "time_range": {"kind": "point", "start": day},
        },
    )


@pytest.mark.asyncio
async def test_seleric_backed_metric_builds_evidence_and_pins_owner_agent():
    definition = _seleric_definition()
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "net_profit_all_channels"}]}],
            "seleric.metrics_query": [
                {
                    "rows": [{"net_profit_all_channels": "-51190.98"}],
                    "provenance": {"cube_view": "canonical_pnl", "query_id": "q_1", "catalogue_version": "abc123"},
                }
            ],
        }
    )
    runtime = SimpleNamespace(metrics=_FakeMetrics(definition), mcp=gateway, ontology=None)
    observer = ObserverAgent(runtime)

    result = await observer.observe(_ctx("metric.net_profit"))

    assert result["error_code"] is None
    assert result["evidence"][0]["value"] == pytest.approx(-51190.98)
    assert result["evidence"][0]["source"] == "seleric_mcp.canonical_pnl"
    # observer resolves the measure via the live catalogue, then fetches on
    # behalf of the owning domain agent -- that's what lets the gateway pin
    # the correct module. No local metric -> tool table involved.
    assert gateway.calls[0]["agent_id"] == "finance_agent"
    assert gateway.calls[0]["capability"] == "seleric.metrics_query"
    assert gateway.calls[0]["arguments"]["measures"] == ["net_profit_all_channels"]


@pytest.mark.asyncio
async def test_module_refusal_payload_is_treated_as_missing_not_a_crash():
    definition = _seleric_definition()
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "net_profit"}]}],
            "seleric.metrics_query": [{"error": "outside module", "rows": []}],
        }
    )
    runtime = SimpleNamespace(metrics=_FakeMetrics(definition), mcp=gateway, ontology=None)
    observer = ObserverAgent(runtime)

    result = await observer.observe(_ctx("metric.net_profit"))

    assert result["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert result["evidence"] == []


@pytest.mark.asyncio
async def test_no_catalogue_match_is_treated_as_missing_not_a_crash():
    definition = _seleric_definition()
    # No catalogue_metric in raw → empty search must not invent a measure.
    definition.catalogue_metric = None
    definition.raw = {}
    gateway = _FakeGateway({"seleric.catalogue_search_metrics": [{"matches": []}]})
    runtime = SimpleNamespace(metrics=_FakeMetrics(definition), mcp=gateway, ontology=None)
    observer = ObserverAgent(runtime)

    result = await observer.observe(_ctx("metric.net_profit"))

    assert result["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert result["evidence"] == []
    assert [c["capability"] for c in gateway.calls] == ["seleric.catalogue_search_metrics"]


def _commerce_def(metric_id: str, catalogue: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=metric_id,
        unit="INR",
        version=1,
        formula=catalogue,
        description=metric_id,
        domain="commerce",
        catalogue_metric=catalogue,
        seleric_module=None,
        raw={"catalogue_metric": catalogue},
    )


class _MapMetrics:
    def __init__(self, defs):
        self._defs = {d.id: d for d in defs}

    def get(self, metric_id):
        return self._defs.get(metric_id)


@pytest.mark.asyncio
async def test_observer_fetches_every_allowed_hint():
    gross = _commerce_def("metric.gross_sales", "gross_sales")
    net = _commerce_def("metric.net_sales", "commerce_net_revenue_daily")
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [
                {"matches": [{"id": "gross_sales"}]},
                {"matches": [{"id": "commerce_net_revenue_daily"}]},
            ],
            "seleric.metrics_query": [
                {
                    "rows": [{"gross_sales": "72446.13"}],
                    "provenance": {"cube_view": "commerce_orders", "query_id": "q_g"},
                },
                {
                    "rows": [{"commerce_net_revenue_daily": "61000.0"}],
                    "provenance": {"cube_view": "commerce_daily", "query_id": "q_n"},
                },
            ],
        }
    )
    runtime = SimpleNamespace(metrics=_MapMetrics([gross, net]), mcp=gateway, ontology=None)
    observer = ObserverAgent(runtime)
    result = await observer.observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="What is gross sale and net sale for today",
            mission_lead="commerce_agent",
            payload={
                "metric_id": "metric.gross_sales",
                "metric_hints": ["metric.gross_sales", "metric.net_sales"],
                "allowed_metrics": ["metric.gross_sales", "metric.net_sales"],
                "time_range": {"kind": "point", "start": "2026-09-04"},
            },
        )
    )
    values = {row["metric_or_fact"]: row["value"] for row in result["evidence"]}
    assert values["metric.gross_sales"] == pytest.approx(72446.13)
    assert values["metric.net_sales"] == pytest.approx(61000.0)
    assert result["error_code"] is None
    assert result["llm_calls"] == 0


@pytest.mark.asyncio
async def test_observer_fetches_assigned_area_metrics_without_metric_map():
    """Assigned funnel metrics (from DomainQuestion / hints) are fetched as-is.

    Observer does not call metric_map to expand the domain dump.
    """
    sessions = _commerce_def("metric.sessions", "sessions")
    sessions.domain = "funnel"
    checkout = _commerce_def("metric.checkout_rate", "checkout_rate")
    checkout.domain = "funnel"
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [
                {"matches": [{"id": "sessions"}]},
                {"matches": [{"id": "checkout_rate"}]},
            ],
            "seleric.metrics_query": [
                {
                    "rows": [{"sessions": "1000"}],
                    "provenance": {"cube_view": "funnel_daily", "query_id": "q_s"},
                },
                {
                    "rows": [{"checkout_rate": "0.12"}],
                    "provenance": {"cube_view": "funnel_daily", "query_id": "q_c"},
                },
            ],
        }
    )
    runtime = SimpleNamespace(metrics=_MapMetrics([sessions, checkout]), mcp=gateway, ontology=None)
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="funnel status for website today",
            mission_lead="funnel_agent",
            payload={
                "allowed_metrics": ["metric.sessions", "metric.checkout_rate"],
                "metric_hints": ["metric.sessions", "metric.checkout_rate"],
                "time_range": {"kind": "point", "start": "2026-09-09"},
            },
        )
    )
    values = {row["metric_or_fact"]: row["value"] for row in result["evidence"]}
    assert values["metric.sessions"] == pytest.approx(1000.0)
    assert values["metric.checkout_rate"] == pytest.approx(0.12)
    assert result["error_code"] is None
    assert result["llm_calls"] == 0


@pytest.mark.asyncio
async def test_observer_refuses_domain_dump_without_assigned_metrics():
    sessions = _commerce_def("metric.sessions", "sessions")
    sessions.domain = "funnel"
    runtime = SimpleNamespace(
        metrics=_MapMetrics([sessions]),
        mcp=_FakeGateway({"seleric.catalogue_search_metrics": []}),
        ontology=None,
    )
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="funnel status for website today",
            mission_lead="funnel_agent",
            payload={
                "allowed_metrics": ["metric.sessions", "metric.checkout_rate"],
                "time_range": {"kind": "point", "start": "2026-09-09"},
            },
        )
    )
    assert result["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert result["llm_calls"] == 0
    assert "domain dump" in (result.get("limitations") or [""])[0]


@pytest.mark.asyncio
async def test_observer_sku_list_requests_sku_dimension_not_period_total():
    units = _commerce_def("metric.units_sold", "units_sold")
    units.domain = "product"
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "units_sold"}]}],
            "seleric.catalogue_get_metric": [{"supported_dimensions": ["product_title", "sku"]}],
            "seleric.catalogue_resolve_dimension": [
                {
                    "kind": "ambiguous",
                    "candidates": [
                        {"dimension_id": "sku", "confidence": 1.0},
                        {"dimension_id": "seller_sku", "confidence": 1.0},
                    ],
                }
            ],
            "seleric.metrics_query": [
                {
                    "rows": [
                        {"units_sold": "20", "sku": "SERUM-01"},
                        {"units_sold": "12", "sku": "CLEAN-02"},
                    ],
                    "provenance": {"cube_view": "product_performance", "query_id": "q_sku"},
                }
            ],
        }
    )
    runtime = _runtime_with_llm(metrics=_MapMetrics([units]), mcp=gateway, dimensions=["product_title"])
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="Give me the list of SKUs sold last 30 days",
            mission_lead="product_agent",
            payload={
                "metric_id": "metric.units_sold",
                "allowed_metrics": ["metric.units_sold"],
                "time_range": {"kind": "absolute", "start": "2026-08-16", "end": "2026-09-14"},
            },
        )
    )
    query = gateway.calls[-1]["arguments"]
    assert query["dimensions"] == ["sku"]
    assert query["sort"] == [{"field": "units_sold", "direction": "desc"}]
    skus = [row["dimensions"]["sku"] for row in result["evidence"]]
    assert skus == ["SERUM-01", "CLEAN-02"]


@pytest.mark.asyncio
async def test_observer_ranks_top_products_by_title_not_period_total():
    units = _commerce_def("metric.units_sold", "units_sold")
    units.domain = "product"
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "units_sold"}]}],
            "seleric.catalogue_get_metric": [{"supported_dimensions": ["product_title"]}],
            "seleric.metrics_query": [
                {
                    "rows": [
                        {"units_sold": "20", "product_title": "Serum"},
                        {"units_sold": "12", "product_title": "Cleanser"},
                    ],
                    "provenance": {"cube_view": "product_performance", "query_id": "q_p"},
                }
            ],
        }
    )
    runtime = _runtime_with_llm(metrics=_MapMetrics([units]), mcp=gateway, dimensions=["product_title"])
    observer = ObserverAgent(runtime)
    result = await observer.observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="What is top seeling products fro today",
            mission_lead="product_agent",
            payload={
                "metric_id": "metric.units_sold",
                "allowed_metrics": ["metric.units_sold"],
                "time_range": {"kind": "point", "start": "2026-09-04"},
            },
        )
    )
    query = gateway.calls[-1]["arguments"]
    assert query["dimensions"] == ["product_title"]
    assert query["sort"] == [{"field": "units_sold", "direction": "desc"}]
    assert query["limit"] == 10
    titles = [row["dimensions"]["product_title"] for row in result["evidence"]]
    assert titles == ["Serum", "Cleanser"]
    assert [row["value"] for row in result["evidence"]] == [20.0, 12.0]


@pytest.mark.asyncio
async def test_observer_ranks_best_channel_over_last_n_days_window():
    attr = _commerce_def("metric.attributed_net_revenue", "attributed_net_revenue")
    attr.domain = "attribution"
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "attributed_net_revenue"}]}],
            "seleric.catalogue_get_metric": [{"supported_dimensions": ["lt_channel"]}],
            "seleric.metrics_query": [
                {
                    "rows": [
                        {"attributed_net_revenue": "900", "lt_channel": "meta"},
                        {"attributed_net_revenue": "400", "lt_channel": "google"},
                    ],
                    "provenance": {"cube_view": "order_attribution", "query_id": "q_ch"},
                }
            ],
        }
    )
    runtime = _runtime_with_llm(metrics=_MapMetrics([attr]), mcp=gateway, dimensions=["lt_channel"])
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="What is the best performing channel is the last 3 days",
            mission_lead="attribution_agent",
            payload={
                "metric_id": "metric.attributed_net_revenue",
                "allowed_metrics": ["metric.attributed_net_revenue"],
                "time_range": {"kind": "absolute", "start": "2026-09-02", "end": "2026-09-04"},
            },
        )
    )
    query = gateway.calls[-1]["arguments"]
    assert query["time_range"] == {"start": "2026-09-02", "end": "2026-09-04"}
    assert query["dimensions"] == ["lt_channel"]
    assert query["sort"] == [{"field": "attributed_net_revenue", "direction": "desc"}]
    assert query["limit"] == 10
    channels = [row["dimensions"]["lt_channel"] for row in result["evidence"]]
    assert channels == ["meta", "google"]


@pytest.mark.asyncio
async def test_observer_units_sold_without_top_stays_period_total():
    units = _commerce_def("metric.units_sold", "units_sold")
    units.domain = "product"
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "units_sold"}]}],
            "seleric.metrics_query": [
                {
                    "rows": [{"units_sold": "44"}],
                    "provenance": {"cube_view": "product_performance", "query_id": "q_t"},
                }
            ],
        }
    )
    runtime = SimpleNamespace(metrics=_MapMetrics([units]), mcp=gateway, ontology=None)
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="How many units sold on 2026-08-01?",
            mission_lead="product_agent",
            payload={
                "metric_id": "metric.units_sold",
                "allowed_metrics": ["metric.units_sold"],
                "time_range": {"kind": "point", "start": "2026-08-01"},
            },
        )
    )
    query = next(c["arguments"] for c in gateway.calls if c["capability"] == "seleric.metrics_query")
    assert "dimensions" not in query
    assert result["evidence"][0]["value"] == 44.0
    assert result["evidence"][0]["dimensions"] == {}


def test_registry_match_keeps_both_sales_metrics():
    from seleric_swarm.llm.adapters.fake import hints_from_registry

    assert hints_from_registry("What is gross sale and net sale for today") == [
        "metric.gross_sales",
        "metric.net_sales",
    ]
    assert "metric.cac" in hints_from_registry("What were CAC and net sales on 2026-08-01?")
    assert "metric.net_sales" in hints_from_registry("What were CAC and net sales on 2026-08-01?")
    assert "metric.net_profit" in hints_from_registry("What were net profit and net sales yesterday?")
    assert "metric.attributed_net_revenue" in hints_from_registry(
        "What is the best performing channel is the last 3 days"
    )


def test_registry_match_bundles_funnel_metrics_for_area_status_query():
    """'funnel status' names the whole area (6 metrics), not one measure —
    regression for the mission that used to hard-fail with INSUFFICIENT_EVIDENCE
    because no single registered metric id matched "funnel"."""
    from seleric_swarm.llm.adapters.fake import classify_swarm_query, hints_from_registry

    hints = hints_from_registry("funnel status for website today")
    assert {
        "metric.sessions",
        "metric.checkout_rate",
        "metric.purchase_cvr",
        "metric.atc_rate",
        "metric.pdp_view_rate",
        "metric.atc_to_purchase_rate",
    }.issubset(set(hints))

    classified = classify_swarm_query("funnel status for website today", "Asia/Kolkata", None)
    assert classified["domain_lead"] == "funnel_agent"
    assert classified["unsupported_reason"] is None
    assert classified["intents"] == ["lookup"]


@pytest.mark.asyncio
async def test_observer_uses_resolved_grain_without_ranking_language():
    attr = _commerce_def("metric.attributed_net_revenue", "attributed_net_revenue")
    attr.domain = "attribution"
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "attributed_net_revenue"}]}],
            "seleric.catalogue_get_metric": [{"supported_dimensions": ["lt_channel"]}],
            "seleric.metrics_query": [
                {
                    "rows": [
                        {"attributed_net_revenue": "900", "lt_channel": "meta"},
                        {"attributed_net_revenue": "400", "lt_channel": "google"},
                    ],
                    "provenance": {"cube_view": "order_attribution", "query_id": "q_g"},
                }
            ],
        }
    )
    runtime = _runtime_with_llm(metrics=_MapMetrics([attr]), mcp=gateway, dimensions=["lt_channel"])
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="Get me channel wise report",
            mission_lead="attribution_agent",
            payload={
                "metric_id": "metric.attributed_net_revenue",
                "metric_hints": ["metric.attributed_net_revenue"],
                "allowed_metrics": ["metric.attributed_net_revenue"],
                "resolved_dimensions": ["lt_channel"],
                "time_range": {"kind": "point", "start": "2026-09-02"},
            },
        )
    )
    query = gateway.calls[-1]["arguments"]
    assert query["dimensions"] == ["lt_channel"]
    channels = [row["dimensions"]["lt_channel"] for row in result["evidence"]]
    assert channels == ["meta", "google"]
    assert result["error_code"] is None


@pytest.mark.asyncio
async def test_observer_refuses_period_total_when_grain_unsupported():
    net = _commerce_def("metric.net_sales", "commerce_net_revenue_daily")
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "commerce_net_revenue_daily"}]}],
            "seleric.catalogue_get_metric": [{"supported_dimensions": ["order_date"]}],
            "seleric.metrics_query": [
                {"rows": [{"commerce_net_revenue_daily": "1"}], "provenance": {}},
            ],
        }
    )
    runtime = _runtime_with_llm(metrics=_MapMetrics([net]), mcp=gateway, dimensions=[])
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="Get me channel wise report",
            mission_lead="commerce_agent",
            payload={
                "metric_id": "metric.net_sales",
                "allowed_metrics": ["metric.net_sales"],
                "resolved_dimensions": ["lt_channel"],
                "time_range": {"kind": "point", "start": "2026-09-02"},
            },
        )
    )
    assert "seleric.metrics_query" not in [c["capability"] for c in gateway.calls]
    assert result["error_code"] == "GRAIN_UNSUPPORTED"
    assert result["evidence"] == []


@pytest.mark.asyncio
async def test_observer_meta_ctr_is_aggregate_ratio_not_hourly_slice():
    """Daily Meta CTR is an aggregate ratio. Leftover campaign_objective grain
    (hourly sibling) must not skip the fetch or attach Cube INR."""
    ctr = SimpleNamespace(
        id="meta_ctr",
        unit="ratio",
        version=1,
        formula="clicks / impressions",
        description="Click-through rate",
        domain="performance",
        catalogue_metric="meta_ctr",
        seleric_module=None,
        raw={"catalogue_metric": "meta_ctr"},
    )
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [
                {"matches": [{"id": "meta_ctr_hourly"}, {"id": "meta_ctr"}]}
            ],
            "seleric.catalogue_get_metric": [
                {"supported_dimensions": ["brand_id", "report_date", "campaign_id", "campaign_name"]}
            ],
            "seleric.metrics_query": [
                {
                    "rows": [{"meta_ctr": "0.0179"}],
                    "provenance": {
                        "cube_view": "meta_ad_performance",
                        "query_id": "q_ctr",
                        "currency": "INR",
                    },
                }
            ],
        }
    )
    runtime = SimpleNamespace(metrics=_MapMetrics([ctr]), mcp=gateway, ontology=None, llm=None)
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-ctr",
            task_id="T-1",
            question="What is the meta CTR last 7 days?",
            mission_lead="performance_agent",
            payload={
                "metric_id": "meta_ctr",
                "metric_hints": ["meta_ctr", "meta_ctr_hourly"],
                "allowed_metrics": ["meta_ctr"],
                "resolved_dimensions": ["campaign_objective", "campaign_id"],
                "time_range": {
                    "kind": "absolute",
                    "start": "2026-09-08",
                    "end": "2026-09-14",
                },
            },
        )
    )
    query = next(c["arguments"] for c in gateway.calls if c["capability"] == "seleric.metrics_query")
    assert query["measures"] == ["meta_ctr"]
    assert "dimensions" not in query
    assert result["error_code"] is None
    assert result["evidence"][0]["unit"] == "ratio"
    assert result["evidence"][0]["dimensions"] == {}
    assert result["evidence"][0]["metric_or_fact"] == "meta_ctr"
    assert result.get("requested_dimensions") == []


def _paid_def(metric_id: str, catalogue: str, *, unit: str | None = "ratio") -> SimpleNamespace:
    return SimpleNamespace(
        id=metric_id,
        unit=unit,
        version=1,
        formula=catalogue,
        description=metric_id,
        domain="performance",
        catalogue_metric=catalogue,
        seleric_module=None,
        raw={"catalogue_metric": catalogue},
    )


@pytest.mark.asyncio
async def test_observer_cpc_aggregate_ignores_hourly_sibling_grain():
    cpc = _paid_def("meta_cpc", "meta_cpc")
    gateway = _FakeGateway(
        {
            "seleric.catalogue_get_metric": [
                {
                    "supported_dimensions": ["brand_id", "report_date"],
                    "unit": "INR",
                }
            ],
            "seleric.metrics_query": [
                {
                    "rows": [{"meta_cpc": "12.5"}],
                    "provenance": {"cube_view": "meta_ad_performance", "currency": "INR"},
                }
            ],
        }
    )
    runtime = SimpleNamespace(metrics=_MapMetrics([cpc]), mcp=gateway, ontology=None, llm=None)
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-cpc",
            task_id="T-1",
            question="What is the meta CPC last 7 days?",
            mission_lead="performance_agent",
            payload={
                "metric_id": "meta_cpc",
                "metric_hints": ["meta_cpc", "meta_cpc_hourly"],
                "allowed_metrics": ["meta_cpc"],
                "resolved_dimensions": ["campaign_objective"],
                "time_range": {"kind": "absolute", "start": "2026-09-08", "end": "2026-09-14"},
            },
        )
    )
    query = next(c["arguments"] for c in gateway.calls if c["capability"] == "seleric.metrics_query")
    assert query["measures"] == ["meta_cpc"]
    assert "dimensions" not in query
    assert result["error_code"] is None
    assert result["evidence"][0]["unit"] == "INR"
    assert result.get("requested_dimensions") == []


@pytest.mark.asyncio
async def test_observer_leftover_supported_dim_without_breakdown_stays_aggregate():
    sessions = _commerce_def("metric.sessions", "web_sessions")
    sessions.domain = "funnel"
    sessions.unit = "count"
    gateway = _FakeGateway(
        {
            "seleric.catalogue_get_metric": [
                {"supported_dimensions": ["channel", "brand_id"], "unit": "count"}
            ],
            "seleric.metrics_query": [
                {"rows": [{"web_sessions": "1500"}], "provenance": {"currency": "INR"}}
            ],
        }
    )
    runtime = SimpleNamespace(metrics=_MapMetrics([sessions]), mcp=gateway, ontology=None, llm=None)
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-sess",
            task_id="T-1",
            question="What are sessions last 7 days?",
            mission_lead="funnel_agent",
            payload={
                "metric_id": "metric.sessions",
                "allowed_metrics": ["metric.sessions"],
                "resolved_dimensions": ["channel"],
                "time_range": {"kind": "absolute", "start": "2026-09-08", "end": "2026-09-14"},
            },
        )
    )
    query = next(c["arguments"] for c in gateway.calls if c["capability"] == "seleric.metrics_query")
    assert "dimensions" not in query
    assert result["evidence"][0]["value"] == pytest.approx(1500.0)
    assert result["evidence"][0]["unit"] == "count"
    assert result["evidence"][0]["dimensions"] == {}
    assert result.get("requested_dimensions") == []


@pytest.mark.asyncio
async def test_observer_uses_catalogue_unit_not_cube_currency_for_ratio():
    ctr = _paid_def("meta_ctr", "meta_ctr", unit=None)
    gateway = _FakeGateway(
        {
            "seleric.catalogue_get_metric": [
                {"supported_dimensions": ["report_date"], "unit": "ratio"}
            ],
            "seleric.metrics_query": [
                {
                    "rows": [{"meta_ctr": "0.02"}, {"meta_ctr": "0.03"}],
                    "provenance": {"currency": "INR"},
                }
            ],
        }
    )
    runtime = SimpleNamespace(metrics=_MapMetrics([ctr]), mcp=gateway, ontology=None, llm=None)
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-unit",
            task_id="T-1",
            question="What is CTR today?",
            mission_lead="performance_agent",
            payload={
                "metric_id": "meta_ctr",
                "allowed_metrics": ["meta_ctr"],
                "time_range": {"kind": "point", "start": "2026-09-14"},
            },
        )
    )
    assert result["evidence"][0]["unit"] == "ratio"
    assert result["evidence"][0]["value"] == pytest.approx(0.02)
    assert len(result["evidence"]) == 1


def test_registry_match_resolves_gs_and_roas_abbreviations():
    from seleric_swarm.llm.adapters.fake import classify_swarm_query, hints_from_registry

    assert hints_from_registry("Why has gs increased over the last three days?") == [
        "metric.gross_sales"
    ]
    assert hints_from_registry("Why has roas increased over the last three days?") == [
        "metric.gross_roas"
    ]
    gs = classify_swarm_query("Why has gs increased over the last three days?", "Asia/Kolkata", None)
    assert gs["domain_lead"] == "commerce_agent"
    assert gs["metric_hints"] == ["metric.gross_sales"]
    assert "diagnostic" in gs["intents"]
    roas = classify_swarm_query("Why has roas increased over the last three days?", "Asia/Kolkata", None)
    assert roas["domain_lead"] == "performance_agent"
    assert "metric.gross_roas" in roas["metric_hints"]
    assert "diagnostic" in roas["intents"]
    assert roas["unsupported_reason"] is None

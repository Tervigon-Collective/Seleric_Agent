from types import SimpleNamespace

import pytest

from seleric_swarm.agents.base import AgentContext
from seleric_swarm.agents.intelligence.observer import Agent as ObserverAgent


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
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "net_profit"}]}],
            "seleric.metrics_query": [
                {
                    "rows": [{"net_profit": "-51190.98"}],
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
    assert gateway.calls[0]["capability"] == "seleric.catalogue_search_metrics"
    assert gateway.calls[1]["capability"] == "seleric.metrics_query"
    assert gateway.calls[1]["arguments"]["measures"] == ["net_profit"]


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
async def test_observer_ranks_top_products_by_title_not_period_total():
    units = _commerce_def("metric.units_sold", "units_sold")
    units.domain = "product"
    gateway = _FakeGateway(
        {
            "seleric.catalogue_search_metrics": [{"matches": [{"id": "units_sold"}]}],
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
    runtime = SimpleNamespace(metrics=_MapMetrics([units]), mcp=gateway, ontology=None)
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
                "entities": ["product_title"],
                "time_range": {"kind": "point", "start": "2026-09-04"},
            },
        )
    )
    query = gateway.calls[1]["arguments"]
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
    runtime = SimpleNamespace(metrics=_MapMetrics([attr]), mcp=gateway, ontology=None)
    result = await ObserverAgent(runtime).observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="What is the best performing channel is the last 3 days",
            mission_lead="attribution_agent",
            payload={
                "metric_id": "metric.attributed_net_revenue",
                "allowed_metrics": ["metric.attributed_net_revenue"],
                "entities": ["lt_channel"],
                "time_range": {"kind": "absolute", "start": "2026-09-02", "end": "2026-09-04"},
            },
        )
    )
    query = gateway.calls[1]["arguments"]
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
    assert "dimensions" not in gateway.calls[1]["arguments"]
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

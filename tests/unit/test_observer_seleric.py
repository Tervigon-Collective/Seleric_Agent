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
        version=1,
        formula="gross_profit - operating_cost",
        description="Canonical net profit per the finance P&L definition.",
        domain="finance",
        raw={},
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
    runtime = SimpleNamespace(metrics=_FakeMetrics(definition), mcp=gateway)
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
    runtime = SimpleNamespace(metrics=_FakeMetrics(definition), mcp=gateway)
    observer = ObserverAgent(runtime)

    result = await observer.observe(_ctx("metric.net_profit"))

    assert result["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert result["evidence"] == []


@pytest.mark.asyncio
async def test_no_catalogue_match_is_treated_as_missing_not_a_crash():
    definition = _seleric_definition()
    gateway = _FakeGateway({"seleric.catalogue_search_metrics": [{"matches": []}]})
    runtime = SimpleNamespace(metrics=_FakeMetrics(definition), mcp=gateway)
    observer = ObserverAgent(runtime)

    result = await observer.observe(_ctx("metric.net_profit"))

    assert result["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert result["evidence"] == []

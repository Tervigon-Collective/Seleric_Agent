"""Ontology snapshot: swarm reads Base_Agent OM via MCP, not hardcoded keywords."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from seleric_swarm.agents.base import AgentContext
from seleric_swarm.agents.domains.commerce import Agent as CommerceAgent
from seleric_swarm.agents.intelligence.observer import Agent as ObserverAgent
from seleric_swarm.agents.skeptic.context import SkepticContext, SkepticDeps
from seleric_swarm.agents.skeptic.contracts import Claim, EvidenceArtifact
from seleric_swarm.agents.skeptic.policies import SkepticPolicies
from seleric_swarm.agents.skeptic.validators.metric_validator import MetricValidator
from seleric_swarm.protocols.mcp.servers.seleric_remote import TOOLS
from seleric_swarm.services.ontology import OntologyService
from seleric_swarm.swarm.domain.base import DomainAgent
from seleric_swarm.swarm.domain.configs import COMMERCE


class _FakeGateway:
    def __init__(self, responses):
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict] = []
        self.capabilities = set(responses)

    def module_for(self, agent_id: str) -> str | None:
        return {"commerce_agent": "commerce"}.get(agent_id)

    async def call(self, *, agent_id, capability, arguments):
        self.calls.append({"agent_id": agent_id, "capability": capability, "arguments": arguments})
        return self._responses[capability].pop(0)


class _FakeOntology:
    def __init__(self, related=None, metric=None, module=None):
        self._related = related or {}
        self._metric = metric or {}
        self._module = module or {}
        self.related_calls: list[str] = []
        self.metric_calls: list[str] = []

    async def for_agent(self, agent_id: str) -> dict:
        return dict(self._module)

    async def for_module(self, module: str, *, agent_id: str = "observer_agent") -> dict:
        return dict(self._module)

    async def metric_context(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict:
        self.metric_calls.append(metric_id)
        return dict(self._metric)

    async def related_metrics(self, metric_id: str, *, agent_id: str = "observer_agent") -> dict:
        self.related_calls.append(metric_id)
        return dict(self._related)


class _FakeMetrics:
    def __init__(self, definition):
        self._definition = definition

    def get(self, metric_id):
        return self._definition

    def ids_for_domain(self, domain):
        return [self._definition.id]


_COMMERCE_SLICE = {
    "module": "commerce",
    "domains": [{"name": "Commerce", "om_glossary": "Commerce"}],
    "data_products": [
        {
            "name": "CommercePerformance",
            "domain": "Commerce",
            "primary_serve_table": "clickhouse.default.serve.commerce_orders",
            "contract": "commerce_performance_contract_v1",
            "cube_views": ["commerce_orders"],
        }
    ],
    "entity_clusters": [
        {
            "id": "commerce_order",
            "glossary": "Commerce.CommerceOrder",
            "catalogue_metrics": ["orders", "active_orders"],
        }
    ],
    "attribution_boundary": None,
    "catalogue_version": "abc123",
}


def test_seleric_tools_include_ontology_endpoints():
    assert "catalogue_get_ontology" in TOOLS
    assert "catalogue_related_metrics" in TOOLS
    assert "modules_list" in TOOLS


@pytest.mark.asyncio
async def test_ontology_service_caches_module_slice():
    gw = _FakeGateway(
        {"seleric.catalogue_get_ontology": [_COMMERCE_SLICE, {"should": "not-be-used"}]}
    )
    svc = OntologyService(gw)
    first = await svc.for_module("commerce")
    second = await svc.for_module("commerce")
    assert first["data_products"][0]["name"] == "CommercePerformance"
    assert second is first
    assert len(gw.calls) == 1
    assert gw.calls[0]["arguments"]["module"] == "commerce"


@pytest.mark.asyncio
async def test_ontology_service_extracts_om_block_from_get_metric():
    gw = _FakeGateway(
        {
            "seleric.catalogue_get_metric": [
                {
                    "id": "orders",
                    "openmetadata": {
                        "data_product": "CommercePerformance",
                        "entity_cluster": "commerce_order",
                        "related_metrics": ["active_orders"],
                        "contract": "commerce_performance_contract_v1",
                    },
                }
            ]
        }
    )
    svc = OntologyService(gw)
    om = await svc.metric_context("metric.orders", agent_id="commerce_agent")
    assert om["data_product"] == "CommercePerformance"
    assert om["related_metrics"] == ["active_orders"]
    assert gw.calls[0]["arguments"]["metric_id"] == "orders"


@pytest.mark.asyncio
async def test_ontology_service_offline_returns_empty():
    gw = _FakeGateway({})
    svc = OntologyService(gw)
    assert await svc.for_module("commerce") == {}
    assert await svc.metric_context("orders") == {}


@pytest.mark.asyncio
async def test_ontology_service_swallows_empty_mcp_json():
    class Boom:
        capabilities = {"seleric.catalogue_get_ontology", "seleric.catalogue_get_metric"}

        def module_for(self, agent_id):
            return "commerce"

        async def call(self, **kwargs):
            raise json.JSONDecodeError("Expecting value", "", 0)

    svc = OntologyService(Boom())
    assert await svc.for_agent("commerce_agent") == {}
    assert await svc.for_module("commerce") == {}
    assert await svc.metric_context("orders") == {}


@pytest.mark.asyncio
async def test_ontology_service_does_not_cache_empty_slices():
    gw = _FakeGateway({"seleric.catalogue_get_ontology": [{}, _COMMERCE_SLICE]})
    svc = OntologyService(gw)
    assert await svc.for_module("commerce") == {}
    assert (await svc.for_module("commerce"))["module"] == "commerce"
    assert len(gw.calls) == 2


@pytest.mark.asyncio
async def test_commerce_agent_attaches_ontology_context_from_service_not_keywords():
    runtime = SimpleNamespace(
        metrics=_FakeMetrics(SimpleNamespace(id="metric.net_sales")),
        ontology=_FakeOntology(module=_COMMERCE_SLICE),
    )
    agent = CommerceAgent(runtime)
    result = await agent.run(
        AgentContext(
            mission_id="M-1",
            task_id="T-1",
            question="net sales",
            mission_lead="commerce_agent",
            payload={"metric_id": "metric.net_sales"},
        )
    )
    ctx = result["ontology_context"]
    assert ctx["module"] == "commerce"
    assert ctx["data_products"][0]["name"] == "CommercePerformance"
    assert ctx["entity_clusters"][0]["id"] == "commerce_order"
    assert "order" not in ctx  # keyword fallback is not the live payload


def test_domain_agent_ontology_terms_prefer_live_snapshot():
    agent = DomainAgent(COMMERCE, data_provider=None)
    assert "order" in agent.ontology_terms()  # offline keyword fallback
    agent.attach_ontology(_COMMERCE_SLICE)
    terms = agent.ontology_terms()
    assert "Commerce" in terms
    assert "CommercePerformance" in terms
    assert "commerce_order" in terms
    assert "order" not in terms


@pytest.mark.asyncio
async def test_observer_stamps_data_product_on_seleric_evidence():
    definition = SimpleNamespace(
        id="metric.net_profit",
        unit="INR",
        version=1,
        formula="gross_profit - operating_cost",
        description="Canonical net profit",
        domain="finance",
        raw={},
    )
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
    ontology = _FakeOntology(
        metric={
            "data_product": "CanonicalPnl",
            "contract": "canonical_pnl_contract_v1",
            "entity_cluster": "finance_pnl",
            "domain": "Finance",
        }
    )
    runtime = SimpleNamespace(metrics=_FakeMetrics(definition), mcp=gateway, ontology=ontology)
    observer = ObserverAgent(runtime)
    result = await observer.observe(
        AgentContext(
            mission_id="M-test",
            task_id="T-1",
            question="test",
            mission_lead="finance_agent",
            payload={
                "metric_id": "metric.net_profit",
                "allowed_metrics": ["metric.net_profit"],
                "time_range": {"kind": "point", "start": "2026-09-02"},
            },
        )
    )
    prov = result["evidence"][0]["provenance"]
    assert prov["data_product"] == "CanonicalPnl"
    assert prov["contract"] == "canonical_pnl_contract_v1"
    assert prov["entity_cluster"] == "finance_pnl"
    assert ontology.metric_calls == ["net_profit"]


@pytest.mark.asyncio
async def test_skeptic_flags_paidmedia_metric_used_as_roas_claim():
    ontology = _FakeOntology(
        metric={
            "data_product": "MetaAdsPerformance",
            "domain": "PaidMedia",
            "entity_cluster": "paid_delivery",
            "related_metrics": ["google_spend"],
            "attribution_boundary": True,
            "attribution_policy": "Never answer ROAS from platform-reported fields.",
        }
    )
    ev = EvidenceArtifact(
        evidence_id="EV-1",
        metric_id="meta_spend",
        value=1000,
        source="seleric_mcp.meta_ad_performance",
    )
    ctx = SkepticContext(
        claim=Claim(
            mission_id="MS-1",
            claim_type="numeric",
            statement="Meta ROAS fell because spend rose",
            origin_agent="performance_agent",
        ),
        policies=SkepticPolicies.load(),
        deps=SkepticDeps(ontology=ontology),
        evidence=[ev],
    )
    out = await MetricValidator().run(ctx)
    assert out.status == "WEAK"
    assert any(c.detail.get("wrong_surface") for c in out.challenges)
    assert out.detail["ontology"][0]["data_product"] == "MetaAdsPerformance"


@pytest.mark.asyncio
async def test_diagnostic_records_cluster_neighbors_without_replacing_causal_templates():
    from seleric_swarm.agents.diagnostic.context import DiagnosticContext, DiagnosticDeps
    from seleric_swarm.agents.diagnostic.contracts import DiagnosticRequest
    from seleric_swarm.agents.diagnostic.hypotheses.generator import generate_hypotheses
    from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies

    ontology = _FakeOntology(
        related={
            "related_metrics": ["meta_spend", "google_spend"],
            "entity_cluster": "paid_delivery",
            "data_product": "MetaAdsPerformance",
        }
    )
    ctx = DiagnosticContext(
        request=DiagnosticRequest(mission_id="M-1", question="why did CAC rise?", outcome_metric="metric.cac"),
        policies=DiagnosticPolicies.load(),
        deps=DiagnosticDeps(ontology=ontology),
        outcome_metric="metric.cac",
    )
    hyps = await generate_hypotheses(ctx)
    assert ctx.scratch["semantic_neighbors"] == ["meta_spend", "google_spend"]
    assert ctx.scratch["entity_cluster"] == "paid_delivery"
    assert any(h.treatment_metric == "metric.purchase_cvr" for h in hyps)
    assert ontology.related_calls == ["metric.cac"]

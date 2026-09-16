"""Domain-level question partition after classify + grain — not a new intent LLM."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from seleric_swarm.agents.base import AgentContext
from seleric_swarm.agents.domains.common import domain_mission_update
from seleric_swarm.coordinator.contracts import DomainQuestion, NormalizedQuery
from seleric_swarm.coordinator.decomposition import (
    _caps_for_purpose,
    initial_decomposition,
    merge_domain_retrieves,
)
from seleric_swarm.coordinator.intake import (
    candidate_domains,
    metric_hints_for_mission,
    partition_domain_questions,
)
from seleric_swarm.coordinator.planning.mission_planner import tasks_from_subquestions
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.domain.base import DomainAgent, DomainConfig
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.providers.base import DataResult, MetricReading
from seleric_swarm.swarm.providers.base import ProviderBundle
from seleric_swarm.swarm.specialists.observer import ObserverAgent, _asked_grain, _asked_metrics


def test_partition_splits_product_and_finance(runtime):
    rows = partition_domain_questions(
        original_query="how are SKUs doing and what is net profit",
        metric_ids=["metric.units_sold", "metric.net_profit"],
        grain=["sku"],
        metrics=runtime.metrics,
    )
    by_domain = {dq.domain: dq for dq in rows}
    assert list(by_domain) == ["product", "finance"]
    assert by_domain["product"].metrics == ["metric.units_sold"]
    assert by_domain["finance"].metrics == ["metric.net_profit"]
    assert "sku" not in by_domain["finance"].grain


def test_partition_drops_unsupported_and_unknown_grain():
    def _m(mid: str, domain: str, supported: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            id=mid,
            domain=domain,
            catalogue_metric=mid,
            raw={"supported_dimensions": supported},
        )

    class _Reg:
        def __init__(self) -> None:
            self._d = {
                "metric.units_sold": _m("metric.units_sold", "product", ["sku", "product_title"]),
                "metric.net_profit": _m("metric.net_profit", "finance", ["brand_id"]),
            }

        def get(self, metric_id: str):
            return self._d.get(metric_id)

        def owner_agent_for(self, metric_id: str) -> str | None:
            d = self.get(metric_id)
            return f"{d.domain}_agent" if d else None

    rows = partition_domain_questions(
        original_query="how are SKUs doing and what is net profit",
        metric_ids=["metric.units_sold", "metric.net_profit"],
        grain=["sku"],
        metrics=_Reg(),
    )
    by_domain = {dq.domain: dq for dq in rows}
    assert by_domain["product"].grain == ["sku"]
    assert by_domain["finance"].grain == []


def test_partition_drops_unregistered_metrics(runtime):
    rows = partition_domain_questions(
        original_query="what is flurb",
        metric_ids=["metric.not_a_real_metric", "metric.units_sold"],
        metrics=runtime.metrics,
    )
    assert [dq.domain for dq in rows] == ["product"]
    assert rows[0].metrics == ["metric.units_sold"]


def test_candidate_domains_includes_every_hint_owner(runtime):
    domains = candidate_domains(
        ["lookup"],
        "metric.units_sold",
        runtime.metrics,
        extra_metrics=["metric.net_profit"],
    )
    assert domains == ["product", "finance"]


def test_candidate_domains_primary_only_stays_single(runtime):
    assert candidate_domains(["lookup"], "metric.cac", runtime.metrics) == ["performance"]


def test_candidate_domains_executive_health_is_broad(runtime):
    domains = candidate_domains(
        ["executive_health"],
        "metric.units_sold",
        runtime.metrics,
        extra_metrics=["metric.net_profit"],
    )
    assert domains == ["commerce", "performance", "funnel", "finance"]


def test_metric_hints_keep_primary_first():
    nq = NormalizedQuery(
        original_query="q",
        primary_metric="metric.units_sold",
        secondary_metrics=["metric.net_profit", "metric.units_sold"],
    )
    assert metric_hints_for_mission(nq) == ["metric.units_sold", "metric.net_profit"]


def test_merge_replaces_generic_retrieve_with_one_per_domain():
    nq = NormalizedQuery(
        original_query="how are SKUs doing and what is net profit",
        intents=["lookup"],
        primary_metric="metric.units_sold",
        secondary_metrics=["metric.net_profit"],
        candidate_domains=["product", "finance"],
        domain_questions=[
            DomainQuestion(
                domain="product",
                metrics=["metric.units_sold"],
                grain=["sku"],
                question="[product] retrieve metric.units_sold sliced by sku.",
            ),
            DomainQuestion(
                domain="finance",
                metrics=["metric.net_profit"],
                question="[finance] retrieve metric.net_profit.",
            ),
        ],
    )
    steps = [
        {"purpose": "resolve_metric", "question": "Which metric?", "priority": 9},
        {"purpose": "retrieve", "question": "Retrieve the metric value.", "priority": 7},
        {"purpose": "validate", "question": "Validate provenance.", "priority": 6},
    ]
    merged = merge_domain_retrieves(steps, nq)
    retrieves = [s for s in merged if s["purpose"] == "retrieve"]
    assert [s["branch"] for s in retrieves] == ["product", "finance"]
    assert [s["metadata"]["metrics"] for s in retrieves] == [
        ["metric.units_sold"],
        ["metric.net_profit"],
    ]
    purposes = [s["purpose"] for s in merged]
    assert purposes == ["resolve_metric", "retrieve", "retrieve", "validate"]


def test_merge_skips_executive_health_scan():
    nq = NormalizedQuery(
        original_query="how are we doing",
        intents=["executive_health"],
        domain_questions=[
            DomainQuestion(domain="commerce", metrics=["metric.net_sales"], question="x"),
        ],
    )
    steps = [
        {"purpose": "business_performance", "question": "Revenue?", "priority": 9, "branch": "commerce"},
    ]
    assert merge_domain_retrieves(steps, nq) == steps


@pytest.mark.asyncio
async def test_initial_decomposition_seeds_retrieve_branches():
    nq = NormalizedQuery(
        original_query="units sold and net profit",
        intents=["lookup"],
        primary_metric="metric.units_sold",
        secondary_metrics=["metric.net_profit"],
        candidate_domains=["product", "finance"],
        domain_questions=[
            DomainQuestion(
                domain="product",
                metrics=["metric.units_sold"],
                question="[product] retrieve metric.units_sold.",
            ),
            DomainQuestion(
                domain="finance",
                metrics=["metric.net_profit"],
                question="[finance] retrieve metric.net_profit.",
            ),
        ],
    )
    dec = await initial_decomposition(mission_id="M-dq", normalized=nq)
    retrieves = [sq for sq in dec.subquestions if sq.purpose == "retrieve"]
    assert {sq.branch for sq in retrieves} == {"product", "finance"}
    assert all(sq.metadata.get("domain_question") for sq in retrieves)
    assert "product" in dec.candidate_domains
    assert "finance" in dec.candidate_domains


def test_observer_scopes_extra_metrics_to_current_domain():
    mission = SwarmMission(
        mission_id="M1",
        query="q",
        time_range={},
        context={
            "primary_metric": "metric.units_sold",
            "metric_hints": ["metric.units_sold", "metric.net_profit"],
            "domain_questions": [
                {"domain": "product", "metrics": ["metric.units_sold"], "grain": ["sku"]},
                {"domain": "finance", "metrics": ["metric.net_profit"], "grain": []},
            ],
        },
    )
    assert _asked_metrics(mission, lead="product_agent") == ["metric.units_sold"]
    assert _asked_metrics(mission, lead="finance_agent") == ["metric.net_profit"]
    assert _asked_metrics(mission, lead="commerce_agent") == [
        "metric.units_sold",
        "metric.net_profit",
    ]
    assert _asked_grain(mission, lead="product_agent") == ["sku"]
    assert _asked_grain(mission, lead="finance_agent") == []


@pytest.mark.asyncio
async def test_observer_fetches_one_evidence_row_per_day_when_granularity_is_day():
    """docs/BUG_SHEET.md #14: a diagnostic mission over a multi-day window
    with granularity="day" (Phase 1's classifier field) must fetch one
    Evidence row per day, not a single summed-window aggregate."""

    class _Rec:
        domain = "commerce"

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def fetch(self, *, metric_ids, time_range, dimensions=None, limit=None, sort=None):
            self.calls.append(dict(time_range))
            return DataResult(
                readings=[MetricReading(metric_id=m, value=1.0, data_origin="MCP", synthetic=False) for m in metric_ids],
                events=[],
                missing=[],
            )

        async def events(self, *, time_range):
            return []

    rec = _Rec()
    cfg = DomainConfig(agent_id="commerce_agent", domain="commerce", owned_metrics=["metric.net_sales"], probe_metrics=["metric.net_sales"])
    domain_agent = DomainAgent(cfg, data_provider=rec)
    mission = SwarmMission(
        mission_id="M-daily",
        query="why did net sales drop over the last 5 days",
        time_range={"start": "2026-09-12", "end": "2026-09-16"},
        context={
            "domain_questions": [{"domain": "commerce", "metrics": ["metric.net_sales"], "grain": []}],
            "granularity": "day",
        },
    )
    board = Blackboard("M-daily")
    board.mission_lead = "commerce_agent"
    observer = ObserverAgent(providers=ProviderBundle(data={}, anomaly=None), domains={"commerce_agent": domain_agent})
    posted = await observer.run(board, mission)

    assert [c["start"] for c in rec.calls] == [
        "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15", "2026-09-16",
    ]
    assert all(c["start"] == c["end"] for c in rec.calls)
    assert len(posted) == 5


def test_caps_for_purpose_retrieve_only_observes():
    assert _caps_for_purpose("retrieve") == ["metric_observation", "evidence_collection"]
    assert _caps_for_purpose("resolve_metric") == []
    assert _caps_for_purpose("verify_change") == []
    assert _caps_for_purpose("unknown_slug") == []


@pytest.mark.asyncio
async def test_tasks_skip_coordinator_local_slugs():
    nq = NormalizedQuery(
        original_query="net sales",
        intents=["lookup"],
        primary_metric="metric.net_sales",
        candidate_domains=["commerce"],
        domain_questions=[
            DomainQuestion(
                domain="commerce",
                metrics=["metric.net_sales"],
                question="[commerce] retrieve metric.net_sales.",
            )
        ],
    )
    dec = await initial_decomposition(mission_id="M-local", normalized=nq)
    tasks = tasks_from_subquestions(mission_id="M-local", decomposition=dec)
    assert all(t.task_type == "retrieve" for t in tasks)
    assert all("metric_observation" in t.requested_capabilities for t in tasks)


@pytest.mark.asyncio
async def test_domain_mission_update_exposes_only_assigned_owned_metrics():
    runtime = SimpleNamespace(
        metrics=SimpleNamespace(
            ids_for_domain=lambda d: {
                "product": ["metric.units_sold", "metric.product_net_revenue"],
                "finance": ["metric.net_profit", "metric.product_cost"],
            }[d],
            canonical_id=lambda mid: mid,
        ),
        ontology=None,
    )
    ctx = AgentContext(
        mission_id="M1",
        task_id="T1",
        question="how are SKUs doing and what is net profit",
        mission_lead="product_agent",
        payload={
            "metric_hints": ["metric.units_sold", "metric.net_profit"],
            "domain_questions": [
                {
                    "domain": "product",
                    "metrics": ["metric.units_sold"],
                    "grain": ["sku"],
                },
                {"domain": "finance", "metrics": ["metric.net_profit"], "grain": []},
            ],
        },
    )
    result = await domain_mission_update(
        runtime, agent_id="product_agent", domain="product", ctx=ctx
    )
    assert result["allowed_metrics"] == ["metric.units_sold"]
    assert result["metric_id"] == "metric.units_sold"
    assert result["assigned_grain"] == ["sku"]
    assert result["handoff_needed_metrics"] == ["metric.net_profit"]
    assert "metric.product_net_revenue" not in result["allowed_metrics"]


@pytest.mark.asyncio
async def test_domain_mission_update_rejects_unowned_assigned_metric():
    runtime = SimpleNamespace(
        metrics=SimpleNamespace(
            ids_for_domain=lambda d: ["metric.units_sold"],
            canonical_id=lambda mid: mid,
        ),
        ontology=None,
    )
    ctx = AgentContext(
        mission_id="M1",
        task_id="T1",
        question="q",
        mission_lead="product_agent",
        payload={"metric_id": "metric.net_profit", "metric_hints": ["metric.net_profit"]},
    )
    result = await domain_mission_update(
        runtime, agent_id="product_agent", domain="product", ctx=ctx
    )
    assert result["error_code"] == "ROUTING_UNSUPPORTED"


@pytest.mark.asyncio
async def test_swarm_observe_fetches_only_assigned_metrics_not_peers():
    class _Rec:
        domain = "finance"

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def fetch(self, *, metric_ids, time_range, dimensions=None, limit=None, sort=None):
            self.calls.append({"metric_ids": list(metric_ids), "dimensions": dimensions})
            return DataResult(
                readings=[
                    MetricReading(metric_id=m, value=1.0, data_origin="MCP", synthetic=False)
                    for m in metric_ids
                ],
                events=[],
                missing=[],
                data_origin="MCP",
                synthetic=False,
            )

        async def events(self, *, time_range):
            return []

    finance = _Rec()
    commerce = _Rec()
    commerce.domain = "commerce"
    cfg = DomainConfig(
        agent_id="finance_agent",
        domain="finance",
        owned_metrics=["metric.net_profit", "metric.product_cost"],
        probe_metrics=["metric.net_profit", "metric.product_cost"],
        probe_dimensions=[{}, {"device": "mobile"}],
    )
    agent = DomainAgent(cfg, data_provider=finance, peers={"finance": finance, "commerce": commerce})
    board = Blackboard("M-obs")
    posted = await agent.observe(
        board,
        time_range={"start": "2026-09-01", "end": "2026-09-01"},
        extra_metrics=["metric.net_profit"],
        grain=[],
    )
    assert finance.calls == [
        {"metric_ids": ["metric.net_profit"], "dimensions": None},
    ]
    assert commerce.calls == []
    assert posted


@pytest.mark.asyncio
async def test_swarm_observe_uses_assigned_grain_not_probe_dimensions():
    class _Rec:
        domain = "product"
        calls: list[dict] = []

        async def fetch(self, *, metric_ids, time_range, dimensions=None, limit=None, sort=None):
            self.calls.append({"metric_ids": list(metric_ids), "dimensions": dimensions})
            return DataResult(readings=[], events=[], missing=list(metric_ids))

        async def events(self, *, time_range):
            return []

    rec = _Rec()
    cfg = DomainConfig(
        agent_id="product_agent",
        domain="product",
        owned_metrics=["metric.units_sold"],
        probe_metrics=["metric.units_sold"],
        probe_dimensions=[{}, {"device": "mobile"}],
    )
    agent = DomainAgent(cfg, data_provider=rec)
    await agent.observe(
        Blackboard("M-g"),
        time_range={"start": "2026-09-01"},
        extra_metrics=["metric.units_sold"],
        grain=["sku"],
    )
    assert rec.calls == [{"metric_ids": ["metric.units_sold"], "dimensions": {"sku": ""}}]

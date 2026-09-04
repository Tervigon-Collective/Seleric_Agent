"""Production-wiring adapters: DoWhy causal service, YAML model registry + drift
monitor, constraint-store business rules, and the swarm bridge."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seleric_swarm.agents.skeptic import Claim, SkepticDeps, SkepticValidationRequest
from seleric_swarm.agents.skeptic.contracts import CausalAnalysisArtifact, StrategyArtifact
from seleric_swarm.agents.skeptic.registries import (
    CausalGraph,
    DriftReport,
    InMemoryCausalGraphRegistry,
)
from seleric_swarm.agents.skeptic.services import (
    ConstraintStoreBusinessRuleService,
    DoWhyCausalValidationService,
    InMemoryConstraintStore,
    YamlModelRegistry,
    model_registry_from_yaml,
)
from seleric_swarm.agents.skeptic.services.business_rules import ConstraintSnapshot
from seleric_swarm.agents.skeptic.swarm_bridge import SwarmSkepticSpecialist
from tests.skeptic.conftest import MISSION, strategy_artifact

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------------------- #
# DoWhy causal validation service
# --------------------------------------------------------------------------- #
def _graphs() -> InMemoryCausalGraphRegistry:
    reg = InMemoryCausalGraphRegistry()
    reg.add(CausalGraph("g1", nodes=["page_latency", "purchase"], edges=[("page_latency", "purchase")]))
    return reg


async def test_dowhy_service_reestimates_and_confirms_effect():
    rng = np.random.default_rng(0)
    n = 800
    confounder = rng.normal(size=n)
    treatment = 0.8 * confounder + rng.normal(size=n)
    outcome = -0.6 * treatment + 0.5 * confounder + rng.normal(scale=0.3, size=n)
    df = pd.DataFrame({"page_latency": treatment, "purchase": outcome, "sessions": confounder})

    art = CausalAnalysisArtifact(
        causal_id="CAUS-x", mission_id=MISSION, treatment="page_latency", outcome="purchase",
        graph_id="g1", common_causes=["sessions"], estimator="backdoor.linear_regression",
        estimated_effect=-0.6, confidence_interval=[-0.7, -0.5], passed=True,
        treatment_started_at="2026-09-01T11:00:00+00:00", outcome_started_at="2026-09-01T12:00:00+00:00",
        refutation_results=[{"name": "placebo", "passed": True}, {"name": "rcc", "passed": True}],
    )
    svc = DoWhyCausalValidationService(_graphs())
    res = await svc.validate(art, context={"observations": df})
    assert res.available is True
    assert res.refutations_total >= 2
    assert res.confidence in {"CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS", "STRONGLY_SUPPORTED", "PLAUSIBLE_CAUSAL"}
    assert "dowhy_effect" in res.detail


async def test_dowhy_service_degrades_without_observations():
    art = CausalAnalysisArtifact(
        causal_id="CAUS-y", mission_id=MISSION, treatment="page_latency", outcome="purchase",
        graph_id="g1", common_causes=["sessions"], estimator="backdoor.linear_regression", passed=True,
        refutation_results=[{"name": "a", "passed": True}, {"name": "b", "passed": True}],
    )
    res = await DoWhyCausalValidationService(_graphs()).validate(art, context={})
    assert res.available is True  # metadata audit still ran
    assert any("re-estimation skipped" in i for i in res.issues)


async def test_dowhy_service_rejects_on_sign_flip():
    rng = np.random.default_rng(1)
    n = 600
    t = rng.normal(size=n)
    y = 0.7 * t + rng.normal(scale=0.2, size=n)   # POSITIVE true effect
    df = pd.DataFrame({"page_latency": t, "purchase": y, "sessions": rng.normal(size=n)})
    art = CausalAnalysisArtifact(
        causal_id="CAUS-z", mission_id=MISSION, treatment="page_latency", outcome="purchase",
        graph_id="g1", common_causes=["sessions"], estimator="backdoor.linear_regression",
        estimated_effect=-0.6,   # artifact claims NEGATIVE
        passed=True, treatment_started_at="2026-09-01T11:00:00+00:00",
        outcome_started_at="2026-09-01T12:00:00+00:00",
        refutation_results=[{"name": "a", "passed": True}, {"name": "b", "passed": True}],
    )
    res = await DoWhyCausalValidationService(_graphs()).validate(art, context={"observations": df})
    assert res.confidence == "REJECTED"
    assert any("opposite sign" in i for i in res.issues)


# --------------------------------------------------------------------------- #
# YAML model registry + drift monitor
# --------------------------------------------------------------------------- #
def test_yaml_model_registry_loads_repo_config():
    reg = model_registry_from_yaml()
    assert isinstance(reg, YamlModelRegistry)
    rec = reg.get("forecast.orders.daily")
    assert rec is not None
    assert rec.target == "metric.orders"
    assert rec.model_type == "forecast"


class _RedDriftMonitor:
    async def status_for(self, model_id: str, *, features) -> DriftReport:
        return DriftReport(model_id=model_id, status="red", signals={"psi": 0.42})


async def test_model_validator_consults_drift_monitor_when_artifact_silent(make_agent):
    from tests.skeptic.conftest import forecast_artifact

    fc = forecast_artifact(drift_status=None)
    agent = make_agent([], [fc])
    agent.deps = SkepticDeps(
        model_registry=agent.deps.model_registry,
        causal_graphs=agent.deps.causal_graphs,
        drift_monitor=_RedDriftMonitor(),
    )
    v = await agent.validate_claim(
        SkepticValidationRequest(
            mission_id=MISSION,
            claim=Claim(mission_id=MISSION, claim_type="forecast",
                        statement="CAC will reach 815 in 7d", origin_agent="prediction_agent",
                        forecast_refs=["PRED-1"]),
        )
    )
    assert v.verdict == "REJECT"
    assert any(c.category == "model" and c.severity == "blocking" for c in v.challenges)


# --------------------------------------------------------------------------- #
# Constraint-store business rules
# --------------------------------------------------------------------------- #
async def test_constraint_store_blocks_scale_when_stock_critical():
    store = InMemoryConstraintStore(ConstraintSnapshot(stock_cover_days=3.0, critical_stock_cover_days=7.0))
    svc = ConstraintStoreBusinessRuleService(store)
    strat = StrategyArtifact(strategy_id="S1", mission_id=MISSION,
                             action="Increase paid acquisition spend by 30%", owner_domain="performance")
    violations = await svc.validate_strategy(strat, context={"mission_id": MISSION})
    assert any(v.rule_id == "inventory.no_scale_when_stock_critical" and v.severity == "blocking" for v in violations)


async def test_constraint_store_margin_floor():
    store = InMemoryConstraintStore(
        ConstraintSnapshot(contribution_margin_floor=0.20, current_contribution_margin=0.12)
    )
    svc = ConstraintStoreBusinessRuleService(store)
    strat = StrategyArtifact(strategy_id="S2", mission_id=MISSION,
                             action="Increase discount to 30% to lift conversion")
    violations = await svc.validate_strategy(strat, context={})
    assert any(v.rule_id == "finance.margin_floor" and v.severity == "blocking" for v in violations)


async def test_constraint_store_budget_delta_cap():
    store = InMemoryConstraintStore(ConstraintSnapshot(max_budget_delta_pct=20.0))
    svc = ConstraintStoreBusinessRuleService(store)
    strat = StrategyArtifact(strategy_id="S2b", mission_id=MISSION,
                             action="Increase paid media spend by 45%")
    violations = await svc.validate_strategy(strat, context={})
    assert any(v.rule_id == "finance.budget_delta_cap" and v.severity == "warning" for v in violations)


async def test_constraint_store_clean_snapshot_no_violations():
    svc = ConstraintStoreBusinessRuleService(InMemoryConstraintStore(ConstraintSnapshot(stock_cover_days=45.0)))
    strat = StrategyArtifact(strategy_id="S3", mission_id=MISSION, action="Roll back DEP-4471")
    assert await svc.validate_strategy(strat, context={}) == []


async def test_skeptic_with_constraint_store_rejects(make_agent):
    store = InMemoryConstraintStore(ConstraintSnapshot(stock_cover_days=2.0))
    agent = make_agent([], [strategy_artifact(action="Scale acquisition spend 40%", mechanism_fit="low",
                                              owner_domain="performance")])
    agent.deps = SkepticDeps(
        metric_registry=agent.deps.metric_registry,
        model_registry=agent.deps.model_registry,
        causal_graphs=agent.deps.causal_graphs,
        rules=ConstraintStoreBusinessRuleService(store),
    )
    v = await agent.validate_claim(
        SkepticValidationRequest(
            mission_id=MISSION,
            claim=Claim(mission_id=MISSION, claim_type="recommendation",
                        statement="we should scale acquisition spend 40%", origin_agent="strategy_agent",
                        strategy_refs=["STRAT-1"]),
        )
    )
    assert v.verdict == "REJECT"
    assert any(t.preferred_domain == "inventory" for t in v.required_followups)


# --------------------------------------------------------------------------- #
# Swarm bridge
# --------------------------------------------------------------------------- #
async def test_swarm_bridge_writes_skeptic_artifact():
    from seleric_swarm.swarm.artifacts import Causal, Evidence
    from seleric_swarm.swarm.blackboard import Blackboard
    from seleric_swarm.swarm.mission import SwarmMission

    bb = Blackboard("MS-bridge")
    bb.mission_lead = "technical_agent"
    ev = Evidence.new(mission_id="MS-bridge", created_by="observer@funnel_agent",
                      metric_or_fact="metric.purchase_cvr", value=2.03, baseline=2.95, source="fixture")
    ev.mark_synthetic()
    bb.post(ev)
    c = Causal.new(mission_id="MS-bridge", created_by="diagnostic_agent",
                   treatment="metric.mobile_lcp_seconds", outcome="metric.purchase_cvr",
                   graph_id="causal.funnel_purchase.v1", common_causes=["metric.sessions", "device"],
                   estimator="backdoor.linear_regression", effect=-0.62, effect_ci=[-0.81, -0.44],
                   refutations=[{"name": "placebo_treatment", "passed": True},
                                {"name": "random_common_cause", "passed": True}],
                   passed=True)
    c.mark_synthetic()
    bb.post(c)

    mission = SwarmMission(mission_id="MS-bridge", query="why did CVR drop?", time_range={},
                           initial_lead="technical_agent", intents={"diagnostic"})
    spec = SwarmSkepticSpecialist()
    assert spec.policy(bb, mission) is True
    ids = await spec.run(bb, mission)
    art = bb.get(ids[0])
    assert art["artifact_type"] == "skeptic"
    assert art["verdict"] in {"PASS", "REVISE", "REJECT"}
    assert art["synthetic"] is True
    assert any(q.startswith("trust:") for q in art["quality_flags"])

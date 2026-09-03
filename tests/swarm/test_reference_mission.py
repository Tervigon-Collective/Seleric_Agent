"""Reference mission: the two-axis swarm must dynamically discover the cause of a
CAC increase, transferring domain leadership Performance -> Funnel -> Technical
based on evidence, then forecast, recommend and pass the Skeptic.

Everything runs on deterministic fixture/template providers - no LLM, no network.
"""

from __future__ import annotations

import pytest

from seleric_swarm.swarm.orchestrator import classify_intents, run_swarm_mission

QUERY = "Why has our CAC increased for the last three days, what happens if this continues, and what should we do?"


@pytest.mark.asyncio
async def test_cac_reference_mission_transfers_leadership_on_evidence(runtime):
    res = await run_swarm_mission(runtime, query=QUERY, timezone="Asia/Kolkata", as_of="2026-09-03")

    # dynamic team assembly from intents
    assert classify_intents(QUERY) == {"diagnostic", "predictive", "prescriptive"}
    assert res.initial_mission_lead == "performance_agent"

    # evidence-driven leadership chain: Performance -> Funnel -> Technical
    chain = [res.initial_mission_lead] + [h["to_agent"] for h in res.handoff_history]
    assert chain == ["performance_agent", "funnel_agent", "technical_agent"]
    assert res.mission_lead == "technical_agent"
    assert res.leadership_epoch == 2
    for h in res.handoff_history:
        assert h["evidence_refs"], "each transfer must carry evidence"
        assert h["reason"] and h["unresolved_question"]

    # intelligence pipeline produced every artifact type
    assert res.artifacts["anomaly"], "anomaly detection ran"
    assert res.artifacts["hypothesis"] and res.artifacts["causal"]
    assert res.artifacts["prediction"], "predictive intent -> forecast"
    assert res.artifacts["strategy"], "prescriptive intent -> strategy"
    assert res.artifacts["skeptic"], "skeptic reviewed"

    assert res.status == "completed"
    assert res.synthetic is True
    assert any("SYNTHETIC" in lim for lim in res.limitations)
    assert "PROTOTYPE OUTPUT" in res.final_response
    assert "performance_agent -> funnel_agent -> technical_agent" in res.final_response


@pytest.mark.asyncio
async def test_reference_mission_retains_frontend_regression_hypothesis(runtime):
    res = await run_swarm_mission(runtime, query=QUERY, as_of="2026-09-03")
    assert res.artifacts["causal"], "a causal artifact was produced"
    # the synthesized answer names the mechanism and the recommended rollback
    text = res.final_response.lower()
    assert "deploy" in text or "frontend" in text
    assert "roll back" in text or "rollback" in text or "hotfix" in text
    assert "skeptic verdict: pass" in text


@pytest.mark.asyncio
async def test_no_anomaly_scenario_keeps_leadership(runtime):
    """If media metrics are NOT quiet, Performance should keep leadership - proving
    the transfer is evidence-driven, not scripted. ``load_scenario`` re-reads from
    disk each call, so mutating the returned dict here does not leak."""
    from seleric_swarm.swarm.providers import fixtures as fx

    scenario = fx.load_scenario("cac_regression")
    # make CPM spike hard so Performance owns the frontier
    scenario["domains"]["performance"]["metrics"]["metric.cpm"]["current"] = 145.0
    # and calm the funnel so there is no downstream anomaly
    for m in scenario["domains"]["funnel"]["metrics"].values():
        m["current"] = m["baseline"]
    scenario["domains"]["funnel"]["segments"]["device"]["mobile"]["metric.purchase_cvr"]["current"] = 2.95

    bundle = fx.ProviderBundle(
        data={d: fx.FixtureDataProvider(d, scenario) for d in scenario["domains"]},
        anomaly=fx.TemplateAnomalyDetector(),
        causal=fx.TemplateCausalEngine(scenario),
        forecaster=fx.TemplateForecaster(scenario),
        optimizer=fx.TemplateOptimizer(),
        stats=fx.TemplateStatsEngine(scenario),
    )
    res = await run_swarm_mission(runtime, query=QUERY, as_of="2026-09-03", providers=bundle)
    assert res.handoff_history == []
    assert res.mission_lead == "performance_agent"

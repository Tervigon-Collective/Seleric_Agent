"""Skeptic Agent behaviour suite (spec sec. 55-58).

Numbered tests map 1:1 to the master prompt's required scenarios; the trailing
tests cover the three end-to-end scenarios and determinism.
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic import Claim, SkepticValidationRequest
from tests.skeptic.conftest import (
    MISSION,
    anomaly_artifact,
    causal_artifact,
    evidence,
    forecast_artifact,
    strategy_artifact,
)


def _req(claim: Claim, **kw) -> SkepticValidationRequest:
    return SkepticValidationRequest(mission_id=MISSION, claim=claim, **kw)


def _claim(ctype: str, statement: str, **kw) -> Claim:
    return Claim(mission_id=MISSION, claim_type=ctype, statement=statement, origin_agent="commerce_agent", **kw)


# --------------------------------------------------------------------------- #
# 1. numeric claim with valid evidence -> PASS
# --------------------------------------------------------------------------- #
async def test_01_numeric_with_evidence_passes(make_agent):
    agent = make_agent([evidence("EV-1", "metric.net_sales", 89.0, change_pct=-11.0)])
    v = await agent.validate_claim(_req(_claim("numeric", "net sales index was 89", support_refs=["EV-1"])))
    assert v.verdict == "PASS"
    assert v.trust_label in {"PROBABLE", "STRONG", "VERIFIED"}
    assert v.supporting_evidence == ["EV-1"]


# --------------------------------------------------------------------------- #
# 2. numeric claim without evidence -> REVISE or REJECT (policy driven)
# --------------------------------------------------------------------------- #
async def test_02_numeric_without_evidence(make_agent):
    agent = make_agent([])
    v = await agent.validate_claim(_req(_claim("numeric", "net sales fell 11%")))
    assert v.verdict in {"REVISE", "REJECT"}
    assert any(c.category == "evidence" for c in v.challenges)


# --------------------------------------------------------------------------- #
# 3. same metric name, different definitions -> METRIC_SEMANTIC_CONFLICT
# --------------------------------------------------------------------------- #
async def test_03_metric_semantic_conflict_not_factual(make_agent):
    rows = [
        evidence("EV-perf", "metric.cac", 782.0, source="paidmedia.mcp", calc_version="cac.meta_attributed.v3"),
        evidence("EV-fin", "metric.cac", 611.0, source="finance.mcp", calc_version="cac.total_media_new_cust.v2"),
    ]
    agent = make_agent(rows)
    v = await agent.validate_claim(_req(_claim("numeric", "CAC was 782", support_refs=["EV-perf", "EV-fin"])))
    cats = {c.detail.get("contradiction_type") for c in v.challenges}
    assert "metric_semantic_conflict" in cats
    assert "factual_conflict" not in cats
    assert v.verdict == "REVISE"


# --------------------------------------------------------------------------- #
# 4. anomaly from insufficient sample -> REVISE
# --------------------------------------------------------------------------- #
async def test_04_anomaly_insufficient_sample(make_agent):
    an = anomaly_artifact(history_days=5, sample_size=12)
    agent = make_agent([evidence("EV-cvr", "metric.purchase_cvr", 2.35, change_pct=-24.0)], [an])
    v = await agent.validate_claim(
        _req(_claim("anomaly", "purchase CVR dropped abnormally", support_refs=["EV-cvr"], anomaly_refs=["AN-1"]))
    )
    assert v.verdict == "REVISE"
    assert any("NOT_ENOUGH_DATA" in c.description or "insufficient" in c.description.lower()
               for c in v.challenges) or any(g.capability_required == "metric_observation" for g in v.evidence_gaps)


# --------------------------------------------------------------------------- #
# 5. causal claim missing causal artifact -> REVISE
# --------------------------------------------------------------------------- #
async def test_05_causal_missing_artifact(make_agent):
    agent = make_agent([evidence("EV-cac", "metric.cac", 782.0, change_pct=29.5)])
    v = await agent.validate_claim(_req(_claim("causal", "latency caused the CAC increase", support_refs=["EV-cac"])))
    assert v.verdict == "REVISE"
    assert any(g.blocking and g.capability_required == "causal_diagnosis" for g in v.evidence_gaps)


# --------------------------------------------------------------------------- #
# 6. causal direction impossible (effect precedes treatment) -> REJECT
# --------------------------------------------------------------------------- #
async def test_06_causal_direction_impossible(make_agent):
    bad = causal_artifact(
        treatment_started_at="2026-09-01T12:30:00+05:30",
        outcome_started_at="2026-09-01T11:00:00+05:30",
    )
    agent = make_agent([evidence("EV-cvr", "metric.purchase_cvr", 2.03, change_pct=-31.0)], [bad])
    v = await agent.validate_claim(
        _req(_claim("causal", "latency caused the CVR drop", support_refs=["EV-cvr"], causal_refs=["CAUS-1"]))
    )
    assert v.verdict == "REJECT"
    assert any(c.category == "temporal" and c.severity == "blocking" for c in v.challenges)


# --------------------------------------------------------------------------- #
# 7. causal artifact passes refutation -> PASS
# --------------------------------------------------------------------------- #
async def test_07_causal_passes_refutation(make_agent):
    agent = make_agent(
        [evidence("EV-cvr", "metric.purchase_cvr", 2.03, change_pct=-31.0, dims={"device": "mobile"})],
        [causal_artifact()],
    )
    claim = _claim(
        "causal",
        "the mobile latency regression drove the purchase CVR decline",
        support_refs=["EV-cvr"],
        causal_refs=["CAUS-1"],
        metadata={"alternatives_ruled_out": True},
    )
    v = await agent.validate_claim(_req(claim))
    assert v.verdict == "PASS"
    assert v.trust_label in {"PROBABLE", "STRONG", "VERIFIED"}
    assert any("confounding" in lim.lower() for lim in v.limitations)


# --------------------------------------------------------------------------- #
# 8. forecast with model drift red -> REJECT
# --------------------------------------------------------------------------- #
async def test_08_forecast_model_drift_red(make_agent):
    agent = make_agent([], [forecast_artifact(drift_status="red")])
    v = await agent.validate_claim(
        _req(_claim("forecast", "CAC will reach 815 within 7 days", forecast_refs=["PRED-1"]))
    )
    assert v.verdict == "REJECT"
    assert any(c.category == "model" and c.severity == "blocking" for c in v.challenges)


# --------------------------------------------------------------------------- #
# 9. forecast without model metadata -> REVISE
# --------------------------------------------------------------------------- #
async def test_09_forecast_missing_model_metadata(make_agent):
    agent = make_agent([], [forecast_artifact(model_id=None, model_version=None, backtest={})])
    v = await agent.validate_claim(
        _req(_claim("forecast", "CAC will reach 815 within 7 days", forecast_refs=["PRED-1"]))
    )
    assert v.verdict == "REVISE"
    assert any(c.category in {"model", "forecast"} for c in v.challenges)


# --------------------------------------------------------------------------- #
# 10. strategy scales spend while stock cover critical -> REJECT + inventory followup
# --------------------------------------------------------------------------- #
async def test_10_strategy_blocked_by_inventory_rule(make_agent):
    agent = make_agent(
        [],
        [strategy_artifact(action="Increase paid acquisition spend by 30%", mechanism_fit="low", owner_domain="performance")],
        rule_context={"stock_cover_days": 3, "critical_stock_cover_days": 7},
    )
    v = await agent.validate_claim(
        _req(_claim("recommendation", "we should increase paid acquisition spend by 30%", strategy_refs=["STRAT-1"]))
    )
    assert v.verdict == "REJECT"
    assert any(t.preferred_domain == "inventory" for t in v.required_followups)


# --------------------------------------------------------------------------- #
# 11. strategy does not address diagnosed cause -> REJECT
# --------------------------------------------------------------------------- #
async def test_11_strategy_mechanism_mismatch(make_agent):
    agent = make_agent(
        [],
        [strategy_artifact(action="Reduce paid acquisition spend by 30%", mechanism_fit="low")],
    )
    claim = _claim(
        "recommendation",
        "we should reduce paid acquisition spend by 30%",
        strategy_refs=["STRAT-1"],
        metadata={"diagnosed_mechanism": "checkout payment failure"},
    )
    v = await agent.validate_claim(_req(claim))
    assert v.verdict == "REJECT"
    assert any(c.category == "strategy" and c.severity == "blocking" for c in v.challenges)


# --------------------------------------------------------------------------- #
# 12. conflicting source data -> source conflict + follow-up validation task
# --------------------------------------------------------------------------- #
async def test_12_source_conflict(make_agent):
    rows = [
        evidence("EV-shop", "metric.net_sales", 89.0, source="shopify.orders"),
        evidence("EV-erp", "metric.net_sales", 102.0, source="erp.gl"),
    ]
    agent = make_agent(rows)
    v = await agent.validate_claim(
        _req(_claim("numeric", "net sales index was 89", support_refs=["EV-shop", "EV-erp"]))
    )
    assert v.verdict == "REVISE"
    assert any(c.category == "source" for c in v.challenges)
    assert any(t.requested_capability == "cross_source_reconciliation" for t in v.required_followups)


# --------------------------------------------------------------------------- #
# 13. alternative hypothesis remains unresolved -> REVISE
# --------------------------------------------------------------------------- #
async def test_13_unresolved_alternative(make_agent):
    agent = make_agent(
        [evidence("EV-cvr", "metric.purchase_cvr", 2.03, change_pct=-31.0)],
        [causal_artifact()],
    )
    claim = _claim(
        "causal",
        "the latency regression drove the CVR decline",
        support_refs=["EV-cvr"],
        causal_refs=["CAUS-1"],
        metadata={"alternatives_to_test": ["a pricing change reduced conversion"]},
    )
    v = await agent.validate_claim(_req(claim))
    assert v.verdict == "REVISE"
    assert any(a.status == "open" for a in v.alternative_hypotheses)


# --------------------------------------------------------------------------- #
# 14. full end-to-end graph -> deterministic SkepticVerdict structure
# --------------------------------------------------------------------------- #
async def test_14_full_graph_structure_and_determinism(make_agent):
    rows = [evidence("EV-cvr", "metric.purchase_cvr", 2.03, change_pct=-31.0, dims={"device": "mobile"})]
    arts = [causal_artifact(), strategy_artifact()]
    claim = _claim(
        "causal",
        "the mobile latency regression drove the CVR decline; roll back DEP-4471",
        support_refs=["EV-cvr"],
        causal_refs=["CAUS-1"],
        strategy_refs=["STRAT-1"],
        metadata={"alternatives_ruled_out": True},
    )
    v1 = await make_agent(rows, arts).validate_claim(_req(claim))
    v2 = await make_agent(rows, arts).validate_claim(_req(claim))

    assert v1.verdict in {"PASS", "REVISE", "REJECT"}
    assert set(v1.validator_results) >= {"evidence", "provenance", "metric", "contradiction", "causal"}
    assert v1.risk_class.startswith("R")
    assert v1.trust_label in {"VERIFIED", "STRONG", "PROBABLE", "WEAK", "INSUFFICIENT"}
    assert v1.audit["challenge_plan"]
    assert v1.audit["verdict_reasons"]
    # determinism (no LLM in deps)
    assert v1.verdict == v2.verdict
    assert v1.trust_score == v2.trust_score


# --------------------------------------------------------------------------- #
# sec 56. reference mission -> PASS, STRONG, confounding limitation
# --------------------------------------------------------------------------- #
async def test_sec56_reference_mission_passes_strong(make_agent):
    rows = [
        evidence("EV-cac", "metric.cac", 782.0, change_pct=31.0),
        evidence("EV-cvr", "metric.purchase_cvr", 2.35, change_pct=-24.0),
        evidence("EV-mcvr", "metric.purchase_cvr", 2.03, change_pct=-31.0, dims={"device": "mobile"}),
        evidence("EV-dcvr", "metric.purchase_cvr", 3.30, change_pct=-3.0, dims={"device": "desktop", "segment": "control"}),
        evidence("EV-lcp", "metric.mobile_lcp_seconds", 5.8, change_pct=164.0),
    ]
    claim = _claim(
        "causal",
        "a mobile frontend regression increased latency and was the primary driver of the purchase CVR decline, which increased CAC",
        support_refs=["EV-cac", "EV-cvr", "EV-mcvr", "EV-dcvr", "EV-lcp"],
        causal_refs=["CAUS-1"],
        metadata={
            "alternatives_ruled_out": True,
            "segment_effects": [-31.0, -3.0],
            "control_segment_checked": True,
            "impact": "high",
        },
    )
    v = await make_agent(rows, [causal_artifact()]).validate_claim(_req(claim))
    assert v.verdict == "PASS"
    assert v.trust_label in {"STRONG", "VERIFIED"}
    assert any("confounding" in lim.lower() for lim in v.limitations)


# --------------------------------------------------------------------------- #
# sec 57. creative fatigue vs auction pressure not isolable -> REVISE + followups
# --------------------------------------------------------------------------- #
async def test_sec57_creative_fatigue_unisolated(make_agent):
    rows = [
        evidence("EV-ctr", "metric.ctr", 98.0, change_pct=-8.0),
        evidence("EV-freq", "metric.frequency", 3.4, change_pct=22.0),
        evidence("EV-cpm", "metric.cpm", 142.0, change_pct=42.0),
    ]
    claim = _claim(
        "causal",
        "creative fatigue caused the CAC increase",
        support_refs=["EV-ctr", "EV-freq", "EV-cpm"],
        metadata={"alternatives_to_test": ["auction pressure raised CPM independent of fatigue"], "domain": "performance"},
    )
    v = await make_agent(rows).validate_claim(_req(claim))
    assert v.verdict == "REVISE"
    assert len(v.required_followups) >= 2
    assert any(t.requested_capability in {"causal_diagnosis", "hypothesis_test"} for t in v.required_followups)


# --------------------------------------------------------------------------- #
# sec 58. checkout bug diagnosis + reduce Meta budget strategy -> REJECT
# --------------------------------------------------------------------------- #
async def test_sec58_checkout_bug_budget_cut_rejected(make_agent):
    agent = make_agent(
        [],
        [strategy_artifact(action="Reduce Meta budget by 30%", mechanism_fit="low", owner_domain="performance")],
    )
    claim = _claim(
        "recommendation",
        "reduce Meta budget by 30%",
        strategy_refs=["STRAT-1"],
        metadata={"diagnosed_mechanism": "a checkout bug is failing purchases"},
    )
    v = await agent.validate_claim(_req(claim))
    assert v.verdict == "REJECT"
    reason = " ".join(c.description for c in v.challenges).lower()
    assert "mechanism" in reason or "symptom" in reason

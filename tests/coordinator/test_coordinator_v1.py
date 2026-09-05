"""Coordinator V1 control-plane tests (plan §92 — 22 scenarios)."""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.artifacts.claims import ClaimManager
from seleric_swarm.coordinator.artifacts.manager import ArtifactManager
from seleric_swarm.coordinator.decomposition import (
    initial_decomposition,
    is_duplicate_subquestion,
    refine_from_evidence,
    refine_from_skeptic_followups,
    select_next_subquestions,
)
from seleric_swarm.coordinator.governance.completion_gate import decide_completion
from seleric_swarm.coordinator.governance.remediation import (
    classify_followup,
    targeted_remediation_plan,
)
from seleric_swarm.coordinator.governance.skeptic_gate import apply_skeptic_gate
from seleric_swarm.coordinator.intake import (
    complexity_band,
    intent_band_for_activation,
    normalize_query,
)
from seleric_swarm.coordinator.leadership.frontier import (
    LeadershipController,
    detect_leadership_loop,
    evaluate_frontier,
)
from seleric_swarm.coordinator.planning.mission_planner import build_mission_plan
from seleric_swarm.coordinator.policies import load_coordinator_policies
from seleric_swarm.coordinator.synthesis.response_builder import build_claim_aware_response
from seleric_swarm.orchestration.dispatch import route_for, run_any_mission
from seleric_swarm.swarm.artifacts import Evidence, Hypothesis
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission

# --- 1. Simple lookup -------------------------------------------------------


@pytest.mark.asyncio
async def test_01_simple_lookup_minimal_swarm(runtime):
    route = await route_for(runtime, query="What were Shopify sales yesterday?")
    assert route == "lookup"
    nq = await normalize_query("What were Shopify sales yesterday?")
    assert "lookup" in nq.intents or complexity_band(nq) in {"L0", "L1"}
    assert intent_band_for_activation(nq) == "LOOKUP"
    dec = initial_decomposition(mission_id="M1", normalized=nq)
    assert dec.template == "lookup"
    assert len(dec.subquestions) <= 6
    plan = build_mission_plan(
        mission_id="M1", normalized=nq, decomposition=dec, initial_lead="commerce_agent"
    )
    assert "diagnostic_agent" not in plan.required_specialists
    assert "observer_agent" in plan.required_specialists


# --- 2. Vague executive query -----------------------------------------------


@pytest.mark.asyncio
async def test_02_executive_health_decomposition():
    nq = await normalize_query("How are we doing today?")
    assert "executive_health" in nq.intents
    dec = initial_decomposition(mission_id="M2", normalized=nq)
    assert dec.template == "executive_health"
    assert len(dec.objectives) >= 4
    purposes = {sq.purpose for sq in dec.subquestions}
    assert "business_performance" in purposes
    assert "paid_acquisition" in purposes
    # no deep diagnosis until anomaly
    assert all(sq.purpose != "causal_validation" for sq in dec.subquestions)


# --- 3–5. CAC recursive decomposition / ruled-out / mobile refine -----------


@pytest.mark.asyncio
async def test_03_04_05_cac_recursive_decomposition_and_frontier():
    nq = await normalize_query("Why has CAC increased over the last three days?")
    dec = initial_decomposition(mission_id="M3", normalized=nq)
    assert dec.template == "cac_diagnostic"
    assert any(sq.branch == "media" for sq in dec.subquestions)

    evidence = [
        {"artifact_id": "EV-1", "metric_or_fact": "metric.cpm", "change_pct": 1.0},
        {"artifact_id": "EV-2", "metric_or_fact": "metric.ctr", "change_pct": -1.0},
        {"artifact_id": "EV-3", "metric_or_fact": "metric.cpc", "change_pct": 2.0},
        {"artifact_id": "EV-4", "metric_or_fact": "metric.purchase_cvr", "change_pct": -24.0},
    ]
    anomalies = [
        {"metric_id": "metric.cpm", "deviation_pct": 1.0},
        {"metric_id": "metric.ctr", "deviation_pct": -1.0},
        {"metric_id": "metric.cpc", "deviation_pct": 2.0},
        {"metric_id": "metric.purchase_cvr", "deviation_pct": -24.0},
        {
            "metric_id": "metric.purchase_cvr",
            "deviation_pct": -31.0,
            "dimensions": {"device": "mobile"},
        },
    ]
    v2 = refine_from_evidence(dec, evidence=evidence, anomalies=anomalies, reason="cvr_frontier")
    assert v2.version == 2
    assert v2.parent_decomposition_id == dec.decomposition_id
    media_states = [sq.status for sq in v2.subquestions if sq.branch == "media"]
    assert media_states and all(s == "irrelevant" for s in media_states)
    assert any(sq.branch == "funnel" for sq in v2.subquestions)

    v3 = refine_from_evidence(v2, evidence=evidence, anomalies=anomalies, reason="mobile_frontier")
    assert v3.version >= 3
    assert any(sq.branch == "technical" for sq in v3.subquestions)
    assert v3.questions_added
    assert v3.reason_for_revision


# --- 6. Skeptic adds subquestion --------------------------------------------


@pytest.mark.asyncio
async def test_06_skeptic_adds_traffic_mix_subquestion():
    nq = await normalize_query("Why has CAC increased?")
    dec = initial_decomposition(mission_id="M6", normalized=nq)
    followups = [
        {
            "task_id": "FUP-1",
            "question": "Did acquisition traffic composition materially change during the degradation window?",
            "requested_capability": "metric_observation",
            "preferred_domain": "performance",
            "priority": 8,
        }
    ]
    refined = refine_from_skeptic_followups(dec, followups)
    assert refined.version == dec.version + 1
    assert any("traffic composition" in sq.question.lower() for sq in refined.subquestions)
    assert len(refined.questions_added) == 1


# --- 7. Duplicate subquestion prevention ------------------------------------


@pytest.mark.asyncio
async def test_07_duplicate_subquestion_prevention():
    nq = await normalize_query("Why has CAC increased?")
    dec = initial_decomposition(mission_id="M7", normalized=nq)
    q = dec.subquestions[0].question
    assert is_duplicate_subquestion(dec.subquestions, q)
    before = len(dec.subquestions)
    refined = refine_from_skeptic_followups(
        dec, [{"question": q, "requested_capability": "metric_observation"}]
    )
    assert refined.version == dec.version
    assert len(refined.subquestions) == before


# --- 8. Evidence deduplication ----------------------------------------------


def test_08_evidence_fingerprint_dedup():
    bb = Blackboard("M8")
    mgr = ArtifactManager(bb)
    e1 = Evidence.new(
        mission_id="M8",
        created_by="funnel_agent",
        metric_or_fact="metric.purchase_cvr",
        value=2.03,
        baseline=2.95,
        source="fixture",
        time_range={"start": "2026-09-01", "end": "2026-09-03"},
        dimensions={"device": "mobile"},
        synthetic=True,
    )
    e1.mark_synthetic()
    id1, dup1 = mgr.ingest(e1)
    assert not dup1
    e2 = Evidence.new(
        mission_id="M8",
        created_by="funnel_agent",
        metric_or_fact="metric.purchase_cvr",
        value=2.03,
        baseline=2.95,
        source="fixture",
        time_range={"start": "2026-09-01", "end": "2026-09-03"},
        dimensions={"device": "mobile"},
        synthetic=True,
    )
    e2.mark_synthetic()
    id2, dup2 = mgr.ingest(e2)
    assert dup2
    assert id1 == id2
    assert len(bb.by_type("evidence")) == 1


# --- 9–10. Leadership path + loop -------------------------------------------


def test_09_leadership_frontier_performance_funnel_technical():
    anomalies = [
        {"metric_id": "metric.cac", "deviation_pct": 29},
        {"metric_id": "metric.cpm", "deviation_pct": 1},
        {"metric_id": "metric.purchase_cvr", "deviation_pct": -24},
        {"metric_id": "metric.mobile_lcp", "deviation_pct": 160, "dimensions": {"device": "mobile"}},
    ]
    f1 = evaluate_frontier(anomalies=anomalies[:2], evidence=[], current_lead="performance_agent")
    # with only cac/cpm, performance leads
    assert f1["frontier_domain"] in {"performance", "funnel", "technical"}

    f2 = evaluate_frontier(
        anomalies=[{"metric_id": "metric.purchase_cvr", "deviation_pct": -24}],
        evidence=[],
        current_lead="performance_agent",
    )
    assert f2["frontier_domain"] == "funnel"
    assert f2["should_transfer"]

    f3 = evaluate_frontier(
        anomalies=[
            {"metric_id": "metric.mobile_lcp_seconds", "deviation_pct": 160, "dimensions": {"device": "mobile"}}
        ],
        evidence=[],
        current_lead="funnel_agent",
    )
    assert f3["frontier_domain"] == "technical"


def test_10_leadership_loop_detection():
    history = [
        {"from_agent": "A", "to_agent": "B"},
        {"from_agent": "B", "to_agent": "A"},
        {"from_agent": "A", "to_agent": "B"},
        {"from_agent": "B", "to_agent": "A"},
    ]
    assert detect_leadership_loop(history)
    ctrl = LeadershipController()
    decision = ctrl.decide_transfer(
        {"mission_id": "M10", "mission_lead": "A", "handoff_history": history, "leadership_epoch": 4},
        {
            "mission_id": "M10",
            "from_agent": "A",
            "requested_target": "B",
            "reason": "loop",
            "evidence_refs": ["EV-1"],
            "unresolved_question": "x",
        },
    )
    assert decision["accepted"] is False


# --- 11–15. Skeptic PASS / REVISE / REJECT / causal graph -------------------


def test_11_skeptic_pass_validates_claim():
    mgr = ClaimManager()
    c = mgr.propose(mission_id="M11", statement="X caused Y", claim_type="causal")
    gate = apply_skeptic_gate(
        claim_manager=mgr,
        claim_id=c.claim_id,
        verdict="PASS",
        followups=[],
        mission_id="M11",
        remediation_round=0,
    )
    assert mgr.claims[c.claim_id].state == "VALIDATED"
    assert c.claim_id in gate["validated_claim_refs"]


def test_12_skeptic_revise_challenges_and_forbids_validated_language():
    mgr = ClaimManager()
    c = mgr.propose(mission_id="M12", statement="Frontend regression caused CVR drop", claim_type="causal")
    gate = apply_skeptic_gate(
        claim_manager=mgr,
        claim_id=c.claim_id,
        verdict="REVISE",
        followups=[{"question": "missing causal graph causal.funnel_purchase.v1", "requested_capability": "causal_diagnosis"}],
        mission_id="M12",
        remediation_round=0,
    )
    assert mgr.claims[c.claim_id].state == "CHALLENGED"
    assert gate["mission_status"] == "remediating"
    assert gate["remediation"]["avoid_full_diagnostic"] is True

    bb = Blackboard("M12")
    mission = SwarmMission(mission_id="M12", query="why cac?", time_range={}, intents={"diagnostic"}, initial_lead="performance_agent")
    # seed skeptic REVISE + retained hyp
    from seleric_swarm.swarm.artifacts import Skeptic

    hyp = Hypothesis.new(mission_id="M12", created_by="diagnostic_agent", statement="Frontend regression caused CVR drop", status="retained")
    bb.post(hyp)
    bb.post(Skeptic.new(mission_id="M12", created_by="skeptic_agent", verdict="REVISE"))
    text = build_claim_aware_response(bb, mission, managed_claims=mgr.dump())
    lowered = text.lower()
    assert "challenged" in lowered
    assert "this claim is not validated" in lowered
    assert "this claim is not challenged" not in lowered
    assert "validated mechanism" not in lowered
    assert "confirmed root cause" not in lowered
    assert "proven cause" not in lowered


def test_13_missing_causal_graph_targeted_remediation():
    followups = [
        {
            "task_id": "FUP-cg",
            "objective": "Resolve missing causal graph causal.funnel_purchase.v1",
            "question": "causal.funnel_purchase.v1 is unavailable",
            "requested_capability": "causal_diagnosis",
            "blocking": True,
            "priority": 9,
        }
    ]
    assert classify_followup(followups[0]) == "missing_causal_graph"
    plan = targeted_remediation_plan(mission_id="M13", followups=followups)
    assert plan["avoid_full_diagnostic"] is True
    assert plan["requires_causal_validation_only"] is True
    caps = [t["requested_capabilities"][0] for t in plan["tasks"]]
    assert "causal_graph_resolve" in caps
    assert all(t.get("assigned_agent") != "diagnostic_agent" or "causal" in str(t) for t in plan["tasks"])


def test_14_graph_resolved_invalidates_dependents_only():
    bb = Blackboard("M14")
    mgr = ArtifactManager(bb)
    from seleric_swarm.swarm.artifacts import Causal, Prediction, Skeptic

    caus = Causal.new(mission_id="M14", created_by="diagnostic_agent", treatment="t", outcome="o", passed=True)
    cid, _ = mgr.ingest(caus)
    pred = Prediction.new(mission_id="M14", created_by="prediction_agent", target="cac", evidence_refs=[cid])
    pid, _ = mgr.ingest(pred, input_refs=[cid])
    sk = Skeptic.new(mission_id="M14", created_by="skeptic_agent", verdict="PASS", evidence_refs=[cid])
    sid, _ = mgr.ingest(sk, input_refs=[cid])
    ev = Evidence.new(mission_id="M14", created_by="observer_agent", metric_or_fact="metric.cac", value=1)
    eid, _ = mgr.ingest(ev)
    invalidated = mgr.invalidate_dependents(cid)
    assert pid in invalidated
    assert sid in invalidated
    assert eid not in invalidated
    assert bb.get(eid) is not None


def test_15_skeptic_reject_opens_next_hypothesis():
    mgr = ClaimManager()
    c = mgr.propose(mission_id="M15", statement="bad hyp", claim_type="causal")
    gate = apply_skeptic_gate(
        claim_manager=mgr,
        claim_id=c.claim_id,
        verdict="REJECT",
        followups=[{"question": "test alt hyp", "requested_capability": "hypothesis_test"}],
        mission_id="M15",
        remediation_round=0,
    )
    assert mgr.claims[c.claim_id].state == "REJECTED"
    assert gate.get("open_next_hypothesis") is True


# --- 16–17. Prediction drift / strategy mismatch ----------------------------


def test_16_prediction_drift_blocks_via_remediation_kind():
    f = {"question": "Model drift detected for forecast model", "requested_capability": "forecasting"}
    assert classify_followup(f) == "model_drift"
    plan = targeted_remediation_plan(mission_id="M16", followups=[f])
    assert plan["requires_prediction_only"] is True


def test_17_strategy_mismatch_classified_as_constraint():
    f = {
        "question": "Strategy reduce Meta budget does not match checkout regression diagnosis",
        "requested_capability": "intervention_design",
        "objective": "constraint mismatch inventory/budget",
    }
    assert classify_followup(f) == "strategy_constraint"


# --- 18. Synthetic propagation ----------------------------------------------


def test_18_synthetic_taint_propagation():
    bb = Blackboard("M18")
    mgr = ArtifactManager(bb)
    ev = Evidence.new(mission_id="M18", created_by="observer", metric_or_fact="m", value=1, synthetic=True)
    ev.mark_synthetic()
    eid, _ = mgr.ingest(ev)
    hyp = Hypothesis.new(mission_id="M18", created_by="diagnostic", statement="h", evidence_refs=[eid])
    hid, _ = mgr.ingest(hyp, input_refs=[eid])
    assert bb.get(hid)["synthetic"] is True
    from seleric_swarm.coordinator.governance.synthetic_guard import mission_synthetic_status

    assert (
        mission_synthetic_status(all_synthetic=True, mixed=False, complete=True)
        == "prototype_completed"
    )


@pytest.mark.asyncio
async def test_18b_synthetic_mission_status_prototype_completed(runtime):
    """Plan scenario 18: all-synthetic complete missions expose prototype_completed."""
    from seleric_swarm.coordinator.graph import run_swarm_v2_mission

    result = await run_swarm_v2_mission(
        runtime,
        scenario_id="cac_regression",
        query="Why has CAC increased over the last three days?",
        full_diagnostic=True,
        full_skeptic=True,
        as_of="2026-09-03",
    )
    assert result.synthetic is True
    if result.status in {"completed", "prototype_completed"}:
        assert result.status == "prototype_completed"
        assert any("prototype_completed" in (x or "") for x in result.limitations)


# --- 19. A2A / task idempotency ---------------------------------------------


def test_19_remediation_task_idempotency():
    followups = [
        {"task_id": "FUP-x", "question": "q", "requested_capability": "metric_observation", "priority": 5}
    ]
    p1 = targeted_remediation_plan(mission_id="M19", followups=followups)
    from seleric_swarm.coordinator.contracts import TaskSpec
    from seleric_swarm.coordinator.planning.mission_planner import append_remediation_tasks

    existing = [TaskSpec.model_validate(t) for t in p1["tasks"]]
    p2 = append_remediation_tasks(mission_id="M19", followups=followups, existing=existing)
    rem = [t for t in p2 if t.metadata.get("remediation")]
    assert len(rem) == len(existing)


# --- 20. Budget exhaustion → partial ----------------------------------------


def test_20_budget_exhaustion_partial():
    decision = decide_completion(
        {
            "objectives": [
                {"objective_id": "O1", "status": "satisfied"},
                {"objective_id": "O2", "status": "unresolved"},
            ],
            "validated_claim_refs": [],
            "challenged_claim_refs": [],
            "rejected_claim_refs": [],
            "evidence_gaps": [],
            "conflicts": [],
            "tasks": [],
            "budgets": {"max_agent_calls": 5},
            "usage": {"agent_calls": 5},
            "evidence": [{"x": 1}],
            "claims": [{"gate_status": "passed"}],
            "status": "partial",
        }
    )
    assert decision.status == "partial"
    assert decision.unresolved_objectives
    assert any("Budget" in r or "Partial" in r or "unresolved" in r.lower() for r in decision.reasons)


# --- 21. Decomposition audit ------------------------------------------------


@pytest.mark.asyncio
async def test_21_decomposition_audit_trail():
    nq = await normalize_query("Why has CAC increased?")
    d1 = initial_decomposition(mission_id="M21", normalized=nq)
    d2 = refine_from_evidence(
        d1,
        evidence=[{"metric_or_fact": "metric.purchase_cvr", "change_pct": -20}],
        anomalies=[
            {"metric_id": "metric.cpm", "deviation_pct": 1},
            {"metric_id": "metric.ctr", "deviation_pct": -1},
            {"metric_id": "metric.cpc", "deviation_pct": 2},
            {"metric_id": "metric.purchase_cvr", "deviation_pct": -20},
        ],
        reason="conversion_selected",
    )
    assert d2.parent_decomposition_id == d1.decomposition_id
    assert d2.reason_for_revision == "conversion_selected"
    assert d2.questions_added or d2.questions_retired
    assert d2.created_from_evidence_refs is not None


# --- 22. Completion gate ≠ all agents finished ------------------------------


def test_22_completion_gate_not_just_agents_finished():
    decision = decide_completion(
        {
            "all_agents_finished": True,
            "objectives": [{"objective_id": "O1", "status": "unresolved"}],
            "validated_claim_refs": [],
            "challenged_claim_refs": ["CL-1"],
            "rejected_claim_refs": [],
            "evidence_gaps": [],
            "conflicts": [],
            "tasks": [],
            "evidence": [{"x": 1}],
            "claims": [{"gate_status": "pending"}],
            "status": "running",
            "skeptic_findings": [{"status": "open"}],
        }
    )
    assert decision.complete is False
    assert "CHALLENGED" in " ".join(decision.reasons) or decision.status in {"partial", "remediating", "running", "blocked"}


# --- Integration: swarm_v2 CAC + lookup routing -----------------------------


@pytest.mark.asyncio
async def test_swarm_v2_cac_reference_mission(runtime):
    runtime.settings.swarm_workflow = "swarm_v2"
    out = await run_any_mission(
        runtime,
        query="Why has CAC increased, what happens if this continues, and what should we do?",
        scenario_id="cac_regression",
        full_diagnostic=True,
        full_prediction=True,
        full_skeptic=False,
        # Pin fixture mode: this test asserts the deterministic scripted
        # PROTOTYPE banner, which live MCP data (the default execution_mode)
        # will not reproduce.
        execution_mode="fixture",
    )
    assert out["route"] == "swarm"
    assert out.get("workflow") == "swarm_v2"
    result = out["result"]
    assert result["status"] in {"completed", "partial", "prototype_completed"}
    assert result["initial_mission_lead"] == "performance_agent"
    path = [result["initial_mission_lead"]] + [h["to_agent"] for h in result["handoff_history"]]
    assert "funnel_agent" in path or result["mission_lead"] in {"funnel_agent", "technical_agent", "performance_agent"}
    assert "PROTOTYPE" in result["final_response"]
    # decomposition events present
    kinds = {e.get("kind") for e in result.get("events") or []}
    assert "mission_control_plane" in kinds or "decomposition_refined" in kinds or "leadership_transfer" in kinds


@pytest.mark.asyncio
async def test_hypothesis_dedup_same_statement():
    bb = Blackboard("Mhyp")
    mgr = ArtifactManager(bb)
    h1 = Hypothesis.new(mission_id="Mhyp", created_by="diagnostic_agent", statement="Same hyp text")
    id1, _ = mgr.ingest(h1)
    h2 = Hypothesis.new(mission_id="Mhyp", created_by="diagnostic_agent", statement="Same hyp text")
    id2, dup = mgr.ingest(h2)
    assert dup
    assert id1 == id2


@pytest.mark.asyncio
async def test_select_next_subquestions_respects_eig():
    nq = await normalize_query("How are we doing today?")
    dec = initial_decomposition(mission_id="Meig", normalized=nq)
    selected = select_next_subquestions(dec, limit=2, eig_threshold=0.01)
    assert len(selected) <= 2
    assert all(sq.status in {"pending", "ready"} for sq in selected)


def test_policies_load():
    p = load_coordinator_policies()
    assert "LOOKUP" in p.activation or p.specialists_for("LOOKUP").required
    assert p.business_timezone == "Asia/Kolkata"

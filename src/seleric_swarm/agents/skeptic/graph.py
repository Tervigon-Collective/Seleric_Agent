"""LangGraph workflow for the Skeptic (spec sec. 16).

    load_claim -> classify_claim -> score_risk -> build_challenge_plan
      -> load_required_evidence -> core_audits (evidence/provenance/metric/contradiction)
      -> generate_alternatives -> route_by_claim_type (type-specific validators)
      -> counterfactual_or_stress -> detect_evidence_gaps -> calculate_trust
      -> determine_verdict --PASS--> finalize
                           --REVISE/REJECT--> build_remediation -> finalize

Nodes are thin: they call the deterministic modules and accumulate
``ValidatorOutcome`` objects on ``state['_outcomes']``. The rich
:class:`SkepticContext` rides on ``state['_context']`` (in-process only).
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.contracts import (
    Challenge,
    SkepticVerdict,
    Verdict,
)
from seleric_swarm.agents.skeptic.evidence_gaps import collect_gaps
from seleric_swarm.agents.skeptic.hypothesis.alternative_generator import generate_alternatives
from seleric_swarm.agents.skeptic.intake.claim_classifier import classify_claim
from seleric_swarm.agents.skeptic.intake.risk_scorer import score_risk
from seleric_swarm.agents.skeptic.planning.challenge_planner import build_challenge_plan
from seleric_swarm.agents.skeptic.planning.validation_router import select_validators
from seleric_swarm.agents.skeptic.remediation.task_builder import build_followups
from seleric_swarm.agents.skeptic.scoring.trust_score import score_trust
from seleric_swarm.agents.skeptic.scoring.verdict_engine import decide_verdict
from seleric_swarm.agents.skeptic.state import SkepticState
from seleric_swarm.agents.skeptic.stress.counterfactual import run_counterfactual
from seleric_swarm.agents.skeptic.stress.sensitivity import run_sensitivity
from seleric_swarm.agents.skeptic.validators import CORE_VALIDATORS, TYPE_VALIDATORS


def _ctx(state: SkepticState) -> SkepticContext:
    return state["_context"]


async def _run_validator(cls, ctx: SkepticContext) -> ValidatorOutcome:
    try:
        return await cls().run(ctx)
    except Exception as exc:
        return ValidatorOutcome(validator=getattr(cls, "name", cls.__name__), status="UNAVAILABLE",
                                methodological_issues=[f"validator error: {exc}"])


async def load_claim(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    return {
        "claim": ctx.claim.model_dump(),
        "claim_type": ctx.claim.claim_type,
        "evidence_refs": [e.evidence_id for e in ctx.evidence],
        "status": "running",
        "_t0": time.perf_counter(),
    }


async def classify_claim_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    result = classify_claim(ctx.claim)
    ctx.claim.claim_type = result.claim_type
    patch: dict[str, Any] = {"claim_type": result.claim_type}
    if result.mismatch:
        ctx.risk_context.setdefault("classifier_mismatch", True)
        patch["methodological_issues"] = [
            (
                f"Declared claim_type '{result.declared_type}' is weaker than the wording implies "
                f"('{result.claim_type}'): {result.signals}"
            )
        ]
    return patch


async def score_risk_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    assessment = score_risk(
        ctx.claim,
        policies=ctx.policies,
        causal=ctx.causal,
        forecasts=ctx.forecasts,
        risk_context=ctx.risk_context,
    )
    ctx.risk_score = assessment.score
    ctx.risk_class = assessment.risk_class
    ctx.risk_components = assessment.components
    return {
        "risk_score": assessment.score,
        "risk_class": assessment.risk_class,
        "risk_components": assessment.components,
    }


async def build_plan_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    ctx.challenge_plan = build_challenge_plan(ctx)
    ctx.risk_context["_selected_validators"] = select_validators(ctx)
    return {"challenge_plan": ctx.challenge_plan, "validators_selected": ctx.risk_context["_selected_validators"]}


async def load_evidence_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    return {
        "loaded_evidence": [e.model_dump() for e in ctx.evidence],
        "evidence_refs": [e.evidence_id for e in ctx.evidence],
    }


async def core_audits_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    outcomes: list[ValidatorOutcome] = list(state.get("_outcomes") or [])
    for cls in CORE_VALIDATORS:
        outcomes.append(await _run_validator(cls, ctx))
    return {"_outcomes": outcomes}


async def alternatives_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    alts = await generate_alternatives(ctx)
    if ctx.claim.metadata.get("alternatives_ruled_out") or ctx.risk_context.get("alternatives_ruled_out"):
        for a in alts:
            a.status = "eliminated"
    ctx.risk_context["_alternatives"] = alts
    return {"alternative_hypotheses": [a.model_dump() for a in alts]}


async def type_validators_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    outcomes: list[ValidatorOutcome] = list(state.get("_outcomes") or [])
    for name in ctx.risk_context.get("_selected_validators", []):
        cls = TYPE_VALIDATORS.get(name)
        if cls is not None:
            outcomes.append(await _run_validator(cls, ctx))
    return {"_outcomes": outcomes}


async def stress_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    alts = ctx.risk_context.get("_alternatives", [])
    stress = run_counterfactual(ctx, alts)
    sensitivity = run_sensitivity(ctx)
    outcomes: list[ValidatorOutcome] = list(state.get("_outcomes") or [])
    oc = ValidatorOutcome(validator="stress", status="OK")
    oc.evidence_gaps.extend(stress.gaps)
    oc.detail = {
        "scenarios": stress.scenarios,
        "unresolved_alternatives": stress.unresolved_alternatives,
        "notes": stress.notes,
        "sensitivity": None if sensitivity is None else sensitivity.__dict__,
    }
    if sensitivity is not None and not sensitivity.robust and ctx.claim.claim_type in {"causal", "correlation"}:
        oc.status = "WEAK"
        oc.challenges.append(
            Challenge(category="causal", severity="warning", description=f"Sensitivity: {sensitivity.note}")
        )
        oc.methodological_issues.append(sensitivity.note)
    outcomes.append(oc)
    return {"_outcomes": outcomes}


async def detect_gaps_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    outcomes = state.get("_outcomes") or []
    gaps = collect_gaps(ctx, outcomes)
    ctx.risk_context["_gaps"] = gaps
    return {"evidence_gaps": [g.model_dump() for g in gaps]}


async def trust_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    outcomes = state.get("_outcomes") or []
    alts = ctx.risk_context.get("_alternatives", [])
    result = score_trust(ctx, outcomes, alts, policies=ctx.policies)
    ctx.risk_context["_trust"] = result
    return {"trust_score": result.score, "trust_label": result.label, "trust_components": result.components}


async def verdict_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    outcomes = state.get("_outcomes") or []
    gaps = ctx.risk_context.get("_gaps", [])
    alts = ctx.risk_context.get("_alternatives", [])
    trust = ctx.risk_context["_trust"]
    decision = decide_verdict(ctx, outcomes, gaps, alts, trust.score)
    ctx.risk_context["_decision"] = decision
    return {"verdict": decision.verdict, "limitations": _limitations(ctx, outcomes)}


def _route_after_verdict(state: SkepticState) -> str:
    return "finalize" if state.get("verdict") == "PASS" else "build_remediation"


async def remediation_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    outcomes = state.get("_outcomes") or []
    gaps = ctx.risk_context.get("_gaps", [])
    alts = ctx.risk_context.get("_alternatives", [])
    tasks = build_followups(ctx, str(state["verdict"]), outcomes, gaps, alts)
    cap = ctx.policies.budget("max_followup_rounds") * 4 or 12
    ctx.risk_context["_followups"] = tasks[:cap]
    return {"required_followups": [t.model_dump() for t in tasks[:cap]]}


async def finalize_node(state: SkepticState) -> dict[str, Any]:
    ctx = _ctx(state)
    outcomes: list[ValidatorOutcome] = state.get("_outcomes") or []
    gaps = ctx.risk_context.get("_gaps", [])
    alts = ctx.risk_context.get("_alternatives", [])
    trust = ctx.risk_context["_trust"]
    decision = ctx.risk_context["_decision"]
    followups = ctx.risk_context.get("_followups", [])

    challenges: list[Challenge] = []
    methodological: list[str] = list(state.get("methodological_issues") or [])
    supporting: list[str] = []
    contradictory: list[str] = list(ctx.claim.contradiction_refs)
    for oc in outcomes:
        challenges.extend(oc.challenges)
        methodological.extend(oc.methodological_issues)
        for ch in oc.challenges:
            if ch.detail.get("contradiction_type") in {"factual_conflict", "data_contradiction", "source_conflict"}:
                contradictory.extend(ch.evidence_refs)
    for ev in ctx.evidence:
        supporting.append(ev.evidence_id)

    cap_ch = ctx.policies.budget("max_challenges")
    challenges = sorted(challenges, key=lambda c: {"blocking": 0, "warning": 1, "info": 2}[c.severity])[:cap_ch or None]

    verdict_value: Verdict = state["verdict"]  # type: ignore[assignment]
    verdict_model = SkepticVerdict(
        skeptic_run_id=state.get("skeptic_run_id") or f"SK-{uuid4().hex[:10]}",
        mission_id=ctx.claim.mission_id,
        claim_id=ctx.claim.claim_id,
        claim_type=ctx.claim.claim_type,
        verdict=verdict_value,
        trust_score=trust.score,
        trust_label=trust.label,
        challenges=challenges,
        supporting_evidence=sorted(set(supporting)),
        contradictory_evidence=sorted({c for c in contradictory if c}),
        alternative_hypotheses=alts,
        evidence_gaps=gaps,
        methodological_issues=sorted(set(methodological)),
        required_followups=followups,
        limitations=state.get("limitations") or [],
        validator_results={oc.validator: oc.to_dict() for oc in outcomes},
        risk_score=ctx.risk_score,
        risk_class=ctx.risk_class,
        trust_components=trust.components,
        audit={
            "challenge_plan": ctx.challenge_plan,
            "validators_selected": ctx.risk_context.get("_selected_validators", []),
            "verdict_reasons": decision.reasons,
            "risk_components": ctx.risk_components,
            "synthetic_inputs": ctx.synthetic_inputs(),
            "elapsed_ms": round((time.perf_counter() - float(state.get("_t0") or time.perf_counter())) * 1000, 2),
        },
    )
    return {
        "status": "done",
        "explanation": "",
        "_verdict_model": verdict_model,
        "verdict": verdict_model.verdict,
    }


def _limitations(ctx: SkepticContext, outcomes: list[ValidatorOutcome]) -> list[str]:
    lims: list[str] = []
    if ctx.synthetic_inputs():
        lims.append(
            "One or more inputs are SYNTHETIC (fixture/template). Treat the verdict as a "
            "methodology check, not a business conclusion."
        )
    if ctx.claim.claim_type in {"causal", "correlation"} and ctx.causal:
        lims.append("Unmeasured confounding cannot be completely excluded.")
    for oc in outcomes:
        if oc.status in {"UNAVAILABLE", "INSUFFICIENT"}:
            lims.append(f"{oc.validator} validation was {oc.status.lower()}.")
    return sorted(set(lims))


def build_skeptic_graph():
    g = StateGraph(SkepticState)
    g.add_node("load_claim", load_claim)
    g.add_node("classify_claim", classify_claim_node)
    g.add_node("score_risk", score_risk_node)
    g.add_node("build_plan", build_plan_node)
    g.add_node("load_evidence", load_evidence_node)
    g.add_node("core_audits", core_audits_node)
    g.add_node("generate_alternatives", alternatives_node)
    g.add_node("type_validators", type_validators_node)
    g.add_node("stress", stress_node)
    g.add_node("detect_gaps", detect_gaps_node)
    g.add_node("calculate_trust", trust_node)
    g.add_node("determine_verdict", verdict_node)
    g.add_node("build_remediation", remediation_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "load_claim")
    g.add_edge("load_claim", "classify_claim")
    g.add_edge("classify_claim", "score_risk")
    g.add_edge("score_risk", "build_plan")
    g.add_edge("build_plan", "load_evidence")
    g.add_edge("load_evidence", "core_audits")
    g.add_edge("core_audits", "generate_alternatives")
    g.add_edge("generate_alternatives", "type_validators")
    g.add_edge("type_validators", "stress")
    g.add_edge("stress", "detect_gaps")
    g.add_edge("detect_gaps", "calculate_trust")
    g.add_edge("calculate_trust", "determine_verdict")
    g.add_conditional_edges(
        "determine_verdict",
        _route_after_verdict,
        {"finalize": "finalize", "build_remediation": "build_remediation"},
    )
    g.add_edge("build_remediation", "finalize")
    g.add_edge("finalize", END)
    return g.compile()

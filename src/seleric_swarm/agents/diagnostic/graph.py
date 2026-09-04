"""LangGraph workflow for the Diagnostic Agent.

    START
      -> load_inputs           (evidence + anomalies + outcome metric + degradation start)
      -> generate_hypotheses   (template + constrained LLM, capped)
      -> rank_hypotheses       (deterministic prior)
      -> test_hypotheses       (per-hypothesis plan + deterministic runners)
      -> classify              (hard gates -> reject; score -> testing/reject)
      -> causal_estimate       (top surviving hypothesis -> service -> confidence tier)
      -> finalize              (retain/reject, DiagnosticArtifact + CausalArtifact + Claim[])
      -> END
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from seleric_swarm.agents.diagnostic.causal import estimate_for_hypothesis
from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import DiagnosticResult
from seleric_swarm.agents.diagnostic.hypotheses import generate_hypotheses, rank_hypotheses
from seleric_swarm.agents.diagnostic.intake import resolve_intake
from seleric_swarm.agents.diagnostic.state import DiagnosticState
from seleric_swarm.agents.diagnostic.synthesis import classify_hypothesis, finalize
from seleric_swarm.agents.diagnostic.testing import plan_tests, run_tests


def _ctx(state: DiagnosticState) -> DiagnosticContext:
    return state["_context"]


async def load_inputs(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    await resolve_intake(ctx)
    return {
        "status": "running",
        "_t0": time.perf_counter(),
        "outcome_metric": ctx.outcome_metric,
        "degradation_started_at": ctx.degradation_started_at or "",
        "anomaly_count": len(ctx.anomalies),
        "evidence_count": len(ctx.evidence),
    }


async def generate_node(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    ctx.hypotheses = await generate_hypotheses(ctx)
    return {"hypotheses": [h.model_dump() for h in ctx.hypotheses]}


async def rank_node(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    ctx.hypotheses = rank_hypotheses(ctx, ctx.hypotheses)
    primary = next((h for h in ctx.hypotheses if h.is_primary), None)
    return {
        "hypotheses": [h.model_dump() for h in ctx.hypotheses],
        "primary_hypothesis_id": primary.hypothesis_id if primary else "",
    }


async def test_node(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    for h in ctx.hypotheses:
        plan = plan_tests(ctx, h)
        h.test_results = await run_tests(ctx, h, plan)
    return {"hypotheses": [h.model_dump() for h in ctx.hypotheses]}


async def classify_node(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    for h in ctx.hypotheses:
        classify_hypothesis(ctx, h)
    ctx.hypotheses.sort(key=lambda h: (-h.posterior_score, -h.prior_score, h.statement))
    return {"hypotheses": [h.model_dump() for h in ctx.hypotheses]}


async def causal_node(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    eligible = [
        h for h in ctx.hypotheses
        if h.status in {"testing", "retained"} and h.treatment_metric and h.posterior_score >= 0.5
    ]
    if not eligible:
        eligible = [h for h in ctx.hypotheses if h.is_primary and h.treatment_metric]
    cap = ctx.policies.budget("max_primary_candidates")
    picked = eligible[:cap]

    best: tuple[Any, str, Any] | None = None
    for h in picked:
        artifact, confidence = await estimate_for_hypothesis(ctx, h)
        rank = ctx.policies.confidence_rank(str(confidence))
        if best is None or rank > ctx.policies.confidence_rank(str(best[1])):
            best = (h, str(confidence), artifact)

    if best is None:
        ctx.scratch["causal"] = None
        return {"causal_confidence": "", "causal_ref": ""}
    chosen_h, chosen_conf, chosen_art = best
    ctx.scratch["causal"] = {"hypothesis": chosen_h, "confidence": chosen_conf, "artifact": chosen_art}
    return {"causal_confidence": chosen_conf, "causal_ref": chosen_art.causal_id}


async def finalize_node(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    result = DiagnosticResult(
        diagnostic_run_id=state.get("diagnostic_run_id") or f"DIAG-{int(time.time() * 1000) % 10_000_000}",
        mission_id=ctx.request.mission_id,
        question=ctx.request.question,
        outcome_metric=ctx.outcome_metric,
        hypotheses=ctx.hypotheses,
    )
    causal = ctx.scratch.get("causal")
    result = finalize(
        ctx,
        result,
        causal_hypothesis=causal["hypothesis"] if causal else None,
        causal_artifact=causal["artifact"] if causal else None,
        causal_confidence=causal["confidence"] if causal else None,
    )
    result.audit = {
        "hypotheses_generated": len(ctx.hypotheses),
        "retained": [h.hypothesis_id for h in result.retained()],
        "rejected": [h.hypothesis_id for h in result.rejected()],
        "causal_confidence": causal["confidence"] if causal else None,
        "observations_fitted": ctx.request.observations is not None,
        "elapsed_ms": round((time.perf_counter() - float(state.get("_t0") or time.perf_counter())) * 1000, 2),
    }
    return {
        "status": "done",
        "_result": result,
        "finding": result.finding.model_dump() if result.finding else {},
        "claims": [c.model_dump() for c in result.claims],
        "limitations": result.limitations,
    }


def build_diagnostic_graph():
    g = StateGraph(DiagnosticState)
    g.add_node("load_inputs", load_inputs)
    g.add_node("generate_hypotheses", generate_node)
    g.add_node("rank_hypotheses", rank_node)
    g.add_node("test_hypotheses", test_node)
    g.add_node("classify", classify_node)
    g.add_node("causal_estimate", causal_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "load_inputs")
    g.add_edge("load_inputs", "generate_hypotheses")
    g.add_edge("generate_hypotheses", "rank_hypotheses")
    g.add_edge("rank_hypotheses", "test_hypotheses")
    g.add_edge("test_hypotheses", "classify")
    g.add_edge("classify", "causal_estimate")
    g.add_edge("causal_estimate", "finalize")
    g.add_edge("finalize", END)
    return g.compile()

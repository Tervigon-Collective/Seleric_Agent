"""LangGraph workflow for the Diagnostic Agent.

    START
      -> load_inputs               (evidence + anomalies + outcome metric + degradation start)
      -> identify_candidate_nodes  (causal-graph ancestors of the outcome -> ranked candidates)
      -> causal_estimate           (DoWhy run for every candidate, concurrently)
      -> generate_scenarios        (LLM narrates ONLY the causally-confirmed candidates)
      -> finalize                  (retain/reject by confidence tier; DiagnosticArtifact +
                                     CausalArtifact(s) + Claim[])
      -> END

DoWhy decides WHICH nodes are responsible; the LLM only narrates confirmed
results afterward -- it never proposes a mechanism or picks a root cause.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from seleric_swarm.agents.diagnostic.causal import estimate_for_hypothesis
from seleric_swarm.agents.diagnostic.causal_discovery import identify_candidate_nodes
from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import DiagnosticResult
from seleric_swarm.agents.diagnostic.intake import resolve_intake
from seleric_swarm.agents.diagnostic.scenarios import generate_scenarios
from seleric_swarm.agents.diagnostic.state import DiagnosticState
from seleric_swarm.agents.diagnostic.synthesis import finalize


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


def _route_after_load(state: DiagnosticState) -> str:
    ctx = _ctx(state)
    return "no_anomaly" if ctx.no_confirmed_anomaly else "identify_candidate_nodes"


async def no_anomaly_node(state: DiagnosticState) -> dict[str, Any]:
    """No anomaly evidence and no metric hint - nothing confirmed to diagnose.

    Forcing a root-cause story here would fabricate a diagnosis against a
    hardcoded default metric; return NO_CONFIRMED_ANOMALY instead.
    """
    ctx = _ctx(state)
    no_anomaly_msg = (
        "NO_CONFIRMED_ANOMALY: no anomaly evidence was supplied and no metric was "
        "named, so no root-cause investigation was run."
    )
    result = DiagnosticResult(
        diagnostic_run_id=state.get("diagnostic_run_id") or f"DIAG-{int(time.time() * 1000) % 10_000_000}",
        mission_id=ctx.request.mission_id,
        question=ctx.request.question,
        outcome_metric=ctx.outcome_metric,
        methodology="no_confirmed_anomaly",
        limitations=[no_anomaly_msg],
        synthetic=ctx.synthetic_inputs(),
    )
    return {
        "status": "done",
        "error": "NO_CONFIRMED_ANOMALY",
        "_result": result,
        "finding": {},
        "claims": [],
        "limitations": result.limitations,
    }


async def identify_node(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    ctx.hypotheses = await identify_candidate_nodes(ctx)
    return {"hypotheses": [h.model_dump() for h in ctx.hypotheses]}


async def causal_node(state: DiagnosticState) -> dict[str, Any]:
    """Run DoWhy for every candidate node the causal graph surfaced -- not a
    single LLM-picked guess. This is the step that finds "all responsible
    nodes" the diagnosis is built from.
    """
    ctx = _ctx(state)
    results: list[tuple[Any, str, Any]] = []
    if ctx.hypotheses:
        estimates = await asyncio.gather(*[estimate_for_hypothesis(ctx, h) for h in ctx.hypotheses])
        for h, (artifact, confidence) in zip(ctx.hypotheses, estimates, strict=True):
            results.append((h, str(confidence), artifact))
    ctx.scratch["causal_results"] = results
    if not results:
        return {"causal_confidence": "", "causal_ref": ""}
    best = max(results, key=lambda r: ctx.policies.confidence_rank(r[1]))
    return {"causal_confidence": best[1], "causal_ref": best[2].causal_id}


def _causally_confirmed(ctx: DiagnosticContext, causal_results: list[tuple[Any, str, Any]]) -> list[Any]:
    """Candidates worth narrating: retained, or inconclusive-but-reported.
    Never rejected, never a candidate DoWhy didn't touch."""
    out = []
    for h, confidence, artifact in causal_results:
        if confidence == "REJECTED":
            continue
        if ctx.policies.meets_retain(confidence) or ctx.policies.emit_inconclusive_finding():
            out.append((h, confidence, artifact))
    return out


async def scenarios_node(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    causal_results = ctx.scratch.get("causal_results") or []
    ctx.scratch["scenario_narratives"] = await generate_scenarios(
        ctx, _causally_confirmed(ctx, causal_results)
    )
    return {}


async def finalize_node(state: DiagnosticState) -> dict[str, Any]:
    ctx = _ctx(state)
    result = DiagnosticResult(
        diagnostic_run_id=state.get("diagnostic_run_id") or f"DIAG-{int(time.time() * 1000) % 10_000_000}",
        mission_id=ctx.request.mission_id,
        question=ctx.request.question,
        outcome_metric=ctx.outcome_metric,
        hypotheses=ctx.hypotheses,
    )
    causal_results = ctx.scratch.get("causal_results") or []
    narratives = ctx.scratch.get("scenario_narratives") or {}
    result = finalize(ctx, result, causal_results=causal_results, narratives=narratives)
    result.audit = {
        "candidates_identified": len(ctx.hypotheses),
        "retained": [h.hypothesis_id for h in result.retained()],
        "rejected": [h.hypothesis_id for h in result.rejected()],
        "causal_candidates_estimated": len(causal_results),
        "findings": len(result.findings),
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


def build_diagnostic_graph(*, checkpointer=None):
    g = StateGraph(DiagnosticState)
    g.add_node("load_inputs", load_inputs)
    g.add_node("no_anomaly", no_anomaly_node)
    g.add_node("identify_candidate_nodes", identify_node)
    g.add_node("causal_estimate", causal_node)
    g.add_node("generate_scenarios", scenarios_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "load_inputs")
    g.add_conditional_edges(
        "load_inputs",
        _route_after_load,
        {"no_anomaly": "no_anomaly", "identify_candidate_nodes": "identify_candidate_nodes"},
    )
    g.add_edge("no_anomaly", END)
    g.add_edge("identify_candidate_nodes", "causal_estimate")
    g.add_edge("causal_estimate", "generate_scenarios")
    g.add_edge("generate_scenarios", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer) if checkpointer is not None else g.compile()

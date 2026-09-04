"""LangGraph workflow for the Prediction Agent.

    START
      -> load_inputs         (target metric, horizon, history/trend, drift, causal support)
      -> select_forecast     (registered model -> approved baseline -> INSUFFICIENT)
      -> check_applicability  (regime / in-domain / history sufficiency / drift)
      -> assemble            (ForecastArtifact + confidence + scenarios + Claim[])
      -> END
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from seleric_swarm.agents.prediction.applicability import assess_applicability
from seleric_swarm.agents.prediction.context import PredictionContext
from seleric_swarm.agents.prediction.intake import resolve_intake
from seleric_swarm.agents.prediction.model_selection import select_and_forecast
from seleric_swarm.agents.prediction.state import PredictionState
from seleric_swarm.agents.prediction.synthesis import build_insufficient, finalize


def _ctx(state: PredictionState) -> PredictionContext:
    return state["_context"]


async def load_inputs(state: PredictionState) -> dict[str, Any]:
    ctx = _ctx(state)
    await resolve_intake(ctx)
    return {
        "status": "running",
        "_t0": time.perf_counter(),
        "target_metric": ctx.target_metric,
        "horizon": ctx.horizon,
        "history_points": len(ctx.history),
        "drift_status": ctx.drift_status or "",
    }


async def select_forecast(state: PredictionState) -> dict[str, Any]:
    ctx = _ctx(state)
    run, reasons = await select_and_forecast(ctx)
    ctx.run = run
    ctx.scratch["fallback_reasons"] = reasons
    # the produced forecast's own drift status feeds the applicability check
    if run is not None and run.drift_status and run.drift_status.lower() not in {"n/a", ""}:
        ctx.drift_status = run.drift_status
    return {"source": run.source if run else "insufficient", "drift_status": ctx.drift_status or ""}


async def check_applicability(state: PredictionState) -> dict[str, Any]:
    ctx = _ctx(state)
    if ctx.run is None:
        return {"applicability": "unknown"}
    status, notes = assess_applicability(ctx)
    ctx.scratch["applicability"] = status
    ctx.scratch["applic_notes"] = notes
    return {"applicability": status}


async def assemble(state: PredictionState) -> dict[str, Any]:
    ctx = _ctx(state)
    reasons = ctx.scratch.get("fallback_reasons", [])
    if ctx.run is None:
        result = build_insufficient(ctx, reasons)
    else:
        result = finalize(
            ctx,
            ctx.run,
            ctx.scratch.get("applicability", "unknown"),
            ctx.scratch.get("applic_notes", []),
            reasons,
        )
    result.prediction_run_id = state.get("prediction_run_id") or result.prediction_run_id
    result.audit["elapsed_ms"] = round(
        (time.perf_counter() - float(state.get("_t0") or time.perf_counter())) * 1000, 2
    )
    return {
        "status": "done",
        "_result": result,
        "source": result.source,
        "applicability": result.applicability,
        "confidence": result.confidence,
        "forecast_ref": result.forecast_artifact.forecast_id if result.forecast_artifact else "",
        "scenarios": [s.model_dump() for s in result.scenarios],
        "claims": [c.model_dump() for c in result.claims],
        "limitations": result.limitations,
    }


def build_prediction_graph():
    g = StateGraph(PredictionState)
    g.add_node("load_inputs", load_inputs)
    g.add_node("select_forecast", select_forecast)
    g.add_node("check_applicability", check_applicability)
    g.add_node("assemble", assemble)

    g.add_edge(START, "load_inputs")
    g.add_edge("load_inputs", "select_forecast")
    g.add_edge("select_forecast", "check_applicability")
    g.add_edge("check_applicability", "assemble")
    g.add_edge("assemble", END)
    return g.compile()

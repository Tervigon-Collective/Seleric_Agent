"""Targeted Skeptic remediation — never blind full-agent reruns."""

from __future__ import annotations

from typing import Any, Literal

from seleric_swarm.coordinator.contracts import TaskSpec
from seleric_swarm.coordinator.planning.mission_planner import append_remediation_tasks

RemediationKind = Literal[
    "missing_evidence",
    "metric_semantics",
    "missing_causal_graph",
    "confounder",
    "forecast_metadata",
    "model_drift",
    "strategy_constraint",
    "hypothesis_test",
    "generic",
]


def classify_followup(followup: dict[str, Any]) -> RemediationKind:
    text = f"{followup.get('objective', '')} {followup.get('question', '')} {followup.get('requested_capability', '')}".lower()
    cap = str(followup.get("requested_capability") or "").lower()
    if "causal graph" in text or "causal_graph" in text or cap in {"causal_graph", "causal_graph_resolve"}:
        return "missing_causal_graph"
    if "drift" in text or "model drift" in text:
        return "model_drift"
    if "forecast" in text or "interval" in text or cap == "model_metadata":
        return "forecast_metadata"
    if "constraint" in text or "inventory" in text or "budget" in text:
        return "strategy_constraint"
    if "confound" in text:
        return "confounder"
    if "metric" in text and ("semantic" in text or "definition" in text):
        return "metric_semantics"
    if cap in {"hypothesis_test", "hypothesis_generation"}:
        return "hypothesis_test"
    # Skeptic temporal / control / stratification probes are hypothesis tests, not blind generics.
    if any(
        token in text
        for token in (
            "compare",
            "control",
            "unaffected",
            "high vs low",
            "precedes",
            "timestamp",
            "same window",
            "stratify",
            "desktop",
        )
    ):
        return "hypothesis_test"
    if cap in {"metric_observation", "evidence_collection"} or "evidence" in text or "retrieve" in text:
        return "missing_evidence"
    return "generic"


def targeted_remediation_plan(
    *,
    mission_id: str,
    followups: list[dict[str, Any]],
    existing_tasks: list[TaskSpec] | None = None,
) -> dict[str, Any]:
    """Build targeted remediation tasks from Skeptic follow-ups.

    Critical rule: missing causal graph -> CausalGraphRegistry resolve task,
    NOT a full Diagnostic re-run.
    """
    normalized: list[dict[str, Any]] = []
    kinds: list[str] = []
    for f in followups:
        kind = classify_followup(f)
        kinds.append(kind)
        item = dict(f)
        if kind == "missing_causal_graph":
            item["requested_capability"] = "causal_graph_resolve"
            item["objective"] = item.get("objective") or "Resolve missing causal graph in CausalGraphRegistry"
            item.setdefault("metadata", {})["avoid_full_diagnostic"] = True
        elif kind == "model_drift":
            item["requested_capability"] = "forecasting"
            item.setdefault("metadata", {})["reevaluate_only"] = True
        elif kind == "forecast_metadata":
            item["requested_capability"] = "model_metadata"
        elif kind == "hypothesis_test":
            item["requested_capability"] = "hypothesis_test"
        elif kind == "missing_evidence":
            item.setdefault("requested_capability", "metric_observation")
        normalized.append(item)

    tasks = append_remediation_tasks(
        mission_id=mission_id,
        followups=normalized,
        existing=list(existing_tasks or []),
    )
    # Only the newly appended remediation tasks
    new_tasks = [t for t in tasks if t.metadata.get("remediation")]
    return {
        "kinds": kinds,
        "tasks": [t.model_dump() for t in new_tasks],
        "avoid_full_diagnostic": any(k == "missing_causal_graph" for k in kinds),
        "requires_causal_validation_only": any(k == "missing_causal_graph" for k in kinds),
        "requires_prediction_only": any(k in {"model_drift", "forecast_metadata"} for k in kinds),
    }


async def execute_targeted_remediation(
    *,
    plan: dict[str, Any],
    activate: Any,
    causal_graphs: Any | None = None,
) -> dict[str, Any]:
    """Execute remediation without blindly re-running entire Diagnostic.

    ``activate(agent_id, objective, intent=None)`` is the swarm activator callback.
    """
    results: list[dict[str, Any]] = []
    if plan.get("avoid_full_diagnostic") or plan.get("requires_causal_validation_only"):
        # Resolve causal graph registry dependency first
        graph_id = None
        for t in plan.get("tasks") or []:
            meta = t.get("metadata") or {}
            follow = meta.get("followup") or {}
            # try to extract graph id from question text
            q = str(follow.get("question") or t.get("objective") or "")
            for token in q.replace("'", " ").replace('"', " ").split():
                if token.startswith("causal."):
                    graph_id = token.strip(".,")
                    break
        resolved = False
        if causal_graphs is not None and graph_id:
            try:
                graph = await _maybe_await(causal_graphs.get(graph_id)) if hasattr(causal_graphs, "get") else None
                resolved = graph is not None
            except Exception:
                resolved = False
            results.append({"action": "causal_graph_resolve", "graph_id": graph_id, "resolved": resolved})
        # Rerun ONLY causal validation path on diagnostic (not full hypothesis generation)
        await activate(
            "diagnostic_agent",
            "Re-run causal validation only after dependency resolve",
            "model_request",
            extra={"causal_validation_only": True, "graph_id": graph_id},
        )
        results.append({"action": "causal_validation_only"})
        return {"results": results, "full_diagnostic": False}

    if plan.get("requires_prediction_only"):
        await activate("prediction_agent", "Reevaluate forecast after metadata/drift fix", "model_request")
        results.append({"action": "prediction_reevaluate"})
        return {"results": results, "full_diagnostic": False}

    # Capability-routed domain/observer follow-ups
    for t in plan.get("tasks") or []:
        agent = t.get("assigned_agent") or "observer_agent"
        if agent == "causal_registry":
            continue
        if agent == "diagnostic_agent" and (t.get("metadata") or {}).get("avoid_full_diagnostic"):
            continue
        intent = "challenge" if agent == "skeptic_agent" else "task_request"
        if agent in {"diagnostic_agent", "prediction_agent", "anomaly_agent", "strategy_agent"}:
            intent = "model_request"
        await activate(agent, t.get("objective") or "Remediation follow-up", intent)
        results.append({"action": "activate", "agent": agent, "task_id": t.get("task_id")})
    return {"results": results, "full_diagnostic": False}


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value

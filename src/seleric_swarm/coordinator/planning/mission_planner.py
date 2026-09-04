"""Build executable TaskSpec DAGs from ProblemDecomposition subquestions."""

from __future__ import annotations

import hashlib
from typing import Any

from seleric_swarm.coordinator.contracts import (
    MissionPlan,
    NormalizedQuery,
    ProblemDecomposition,
    SubQuestion,
    TaskSpec,
)
from seleric_swarm.coordinator.intake import complexity_band, intent_band_for_activation
from seleric_swarm.coordinator.policies import CoordinatorPolicies, load_coordinator_policies


def _idempotency_key(mission_id: str, task_type: str, subquestion_id: str | None, agent: str | None) -> str:
    raw = f"{mission_id}|{task_type}|{subquestion_id or ''}|{agent or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _agent_for_capability(cap: str, preferred_domain: str | None) -> str | None:
    mapping = {
        "metric_observation": "observer_agent",
        "evidence_collection": "observer_agent",
        "anomaly_analysis": "anomaly_agent",
        "hypothesis_generation": "diagnostic_agent",
        "hypothesis_test": "diagnostic_agent",
        "causal_diagnosis": "diagnostic_agent",
        "causal_graph_resolve": "causal_registry",
        "forecasting": "prediction_agent",
        "intervention_design": "strategy_agent",
        "challenge": "skeptic_agent",
        "model_metadata": "prediction_agent",
    }
    if cap in mapping:
        return mapping[cap]
    if preferred_domain:
        return f"{preferred_domain}_agent"
    return None


def tasks_from_subquestions(
    *,
    mission_id: str,
    decomposition: ProblemDecomposition,
    subquestions: list[SubQuestion] | None = None,
) -> list[TaskSpec]:
    selected = subquestions if subquestions is not None else [
        sq for sq in decomposition.subquestions if sq.status in {"pending", "ready"}
    ]
    tasks: list[TaskSpec] = []
    prev_id: str | None = None
    for sq in sorted(selected, key=lambda s: (-s.priority, s.question_id)):
        caps = list(sq.required_capabilities) or ["metric_observation"]
        agent = _agent_for_capability(caps[0], sq.branch)
        tid = f"T-{sq.question_id}"
        tasks.append(
            TaskSpec(
                task_id=tid,
                mission_id=mission_id,
                subquestion_id=sq.question_id,
                task_type=sq.purpose,
                objective=sq.question,
                requested_capabilities=caps,
                preferred_domain=sq.branch,
                requested_artifacts=list(sq.required_artifact_types),
                dependencies=[prev_id] if prev_id and sq.priority < 8 else list(sq.dependencies),
                priority=sq.priority,
                blocking=sq.purpose in {"causal_validation", "skeptic_validation", "causal_graph_resolve"},
                idempotency_key=_idempotency_key(mission_id, sq.purpose, sq.question_id, agent),
                status="ready" if sq.status == "ready" else "pending",
                assigned_agent=agent,
                metadata={"branch": sq.branch},
            )
        )
        prev_id = tid
    return tasks


def build_mission_plan(
    *,
    mission_id: str,
    normalized: NormalizedQuery,
    decomposition: ProblemDecomposition,
    initial_lead: str,
    policies: CoordinatorPolicies | None = None,
) -> MissionPlan:
    policies = policies or load_coordinator_policies()
    band = complexity_band(normalized)
    activation = policies.specialists_for(intent_band_for_activation(normalized))
    # Only schedule highest-value ready questions initially
    from seleric_swarm.coordinator.decomposition import select_next_subquestions

    next_qs = select_next_subquestions(
        decomposition, limit=4, eig_threshold=policies.decomposition.eig_stop_threshold
    )
    tasks = tasks_from_subquestions(mission_id=mission_id, decomposition=decomposition, subquestions=next_qs)
    expected = sorted({a for t in tasks for a in t.requested_artifacts})
    return MissionPlan(
        mission_id=mission_id,
        normalized_query=normalized,
        complexity=band,  # type: ignore[arg-type]
        decomposition_ref=decomposition.decomposition_id,
        objectives=list(decomposition.objectives),
        tasks=tasks,
        initial_domain_lead=initial_lead,
        required_specialists=list(activation.required),
        optional_specialists=list(activation.optional),
        expected_artifact_types=expected,
        budgets=policies.budgets,
        plan_version=1,
    )


def validate_plan(plan: MissionPlan) -> list[str]:
    errors: list[str] = []
    ids = {t.task_id for t in plan.tasks}
    for t in plan.tasks:
        for dep in t.dependencies:
            if dep not in ids:
                errors.append(f"missing dependency {dep} for {t.task_id}")
        if not t.idempotency_key:
            errors.append(f"missing idempotency_key for {t.task_id}")
    if not plan.initial_domain_lead:
        errors.append("initial_domain_lead required")
    return errors


def append_remediation_tasks(
    *,
    mission_id: str,
    followups: list[dict[str, Any]],
    existing: list[TaskSpec],
) -> list[TaskSpec]:
    """Map Skeptic FollowUpTasks to targeted TaskSpecs (never blind full-agent reruns)."""
    out = list(existing)
    seen_keys = {t.idempotency_key for t in existing}
    for f in followups:
        cap = str(f.get("requested_capability") or "metric_observation")
        # Normalize causal-graph gaps to registry resolve — not full diagnostic.
        objective = str(f.get("objective") or f.get("question") or "")
        question = str(f.get("question") or objective)
        if "causal graph" in objective.lower() or "causal graph" in question.lower() or cap in {
            "causal_diagnosis",
            "causal_graph",
        }:
            if "missing" in objective.lower() or "missing" in question.lower() or "unavailable" in question.lower():
                cap = "causal_graph_resolve"
        agent = _agent_for_capability(cap, f.get("preferred_domain"))
        key = _idempotency_key(mission_id, cap, f.get("task_id"), agent)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(
            TaskSpec(
                task_id=str(f.get("task_id") or f"REM-{key[:10]}"),
                mission_id=mission_id,
                task_type="remediation",
                objective=objective or question,
                requested_capabilities=[cap],
                preferred_domain=f.get("preferred_domain"),
                requested_artifacts=[],
                priority=int(f.get("priority") or 8),
                blocking=bool(f.get("blocking")),
                idempotency_key=key,
                status="ready",
                assigned_agent=agent,
                metadata={"remediation": True, "followup": f},
            )
        )
    return out

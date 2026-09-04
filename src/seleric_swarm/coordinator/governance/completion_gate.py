"""Evidence- and objective-based completion decisions."""

from __future__ import annotations

from typing import Any

from seleric_swarm.coordinator.contracts import CompletionDecision, MissionStatus
from seleric_swarm.coordinator.governance.completion import assess_completion
from seleric_swarm.coordinator.governance.conflicts import unresolved_blocking


def decide_completion(state: dict[str, Any]) -> CompletionDecision:
    """Completion is NOT 'all agents finished' — objectives + validation matter."""
    objectives = state.get("objectives") or []
    satisfied = [o["objective_id"] for o in objectives if o.get("status") == "satisfied"]
    unresolved = [
        o["objective_id"]
        for o in objectives
        if o.get("status") in {"pending", "unresolved", "blocked"}
    ]

    validated = list(state.get("validated_claim_refs") or [])
    challenged = list(state.get("challenged_claim_refs") or [])
    rejected = list(state.get("rejected_claim_refs") or [])

    blocking_tasks = [
        t.get("task_id") or t.get("id")
        for t in (state.get("tasks") or state.get("remediation_tasks") or [])
        if t.get("blocking") and t.get("status") not in {"done", "answered", "cancelled"}
    ]
    blocking_subs = [
        sq.get("question_id")
        for sq in _all_subquestions(state)
        if sq.get("status") == "blocked"
    ]
    gaps = [g.get("description") or g.get("reason") or str(g) for g in (state.get("evidence_gaps") or []) if g.get("blocking")]
    raw_conflicts = list(state.get("conflicts") or state.get("contradictions") or [])
    blocking_conflicts = unresolved_blocking(raw_conflicts)
    conflicts = [
        c.get("conflict_id") or c.get("type") or str(c)
        for c in blocking_conflicts
    ]

    synthetic = bool(state.get("synthetic"))
    reasons: list[str] = []

    # Skeptic REVISE / challenged claims block "completed"
    if challenged:
        reasons.append("One or more material claims remain CHALLENGED")
    if blocking_tasks:
        reasons.append("Blocking remediation tasks remain")
    if gaps:
        reasons.append("Blocking evidence gaps remain")
    if conflicts:
        reasons.append("Unresolved blocking conflicts remain")
        for c in blocking_conflicts[:3]:
            reasons.append(f"  - [{c.get('type')}] {c.get('description')}")

    # Budget exhaustion → partial
    usage = state.get("usage") or {}
    budgets = state.get("budgets") or {}
    budget_exhausted = False
    usage_checks = (
        ("agent_calls", "max_agent_calls", usage.get("agent_calls", state.get("agent_calls", 0))),
        ("llm_calls", "max_llm_calls", usage.get("llm_calls", state.get("llm_calls", 0))),
        (
            "remediation_rounds",
            "max_remediation_rounds",
            usage.get("remediation_rounds", state.get("remediation_round", 0)),
        ),
        (
            "leadership_transfers",
            "max_leadership_transfers",
            usage.get("leadership_transfers", len(state.get("handoff_history") or [])),
        ),
    )
    for label, limit_key, used in usage_checks:
        limit = budgets.get(limit_key)
        if limit is not None and int(used or 0) >= int(limit):
            budget_exhausted = True
            reasons.append(f"Budget exhausted: {label}")
    if state.get("budget_exhausted") and not budget_exhausted:
        budget_exhausted = True
        reasons.append(state.get("status_reason") or "Budget exhausted")

    assessment = assess_completion(state, state.get("task_graph"))
    coverage = float(assessment.components.get("objective_coverage") or 0.0)
    if objectives:
        coverage = len(satisfied) / max(1, len(objectives))

    complete = (
        not challenged
        and not blocking_tasks
        and not gaps
        and not conflicts
        and (not unresolved or coverage >= 0.9)
        and assessment.decision == "finish"
    )

    # Agents finished alone is insufficient
    agents_done = state.get("all_agents_finished")
    if agents_done and not complete:
        reasons.append("All agents finished but completion gate requirements unmet")

    status: MissionStatus
    if complete:
        status = "prototype_completed" if synthetic else "completed"
    elif budget_exhausted or (satisfied and unresolved):
        status = "partial"
        if not reasons:
            reasons.append("Partial: some objectives satisfied, others unresolved")
    elif state.get("status") == "blocked" or blocking_tasks:
        status = "blocked"
    elif challenged:
        status = "remediating" if state.get("remediation_tasks") else "partial"
    else:
        # Terminal completion evaluation must never leave the mission "running".
        status = "partial"
        if not reasons:
            reasons.append("Mission finished without satisfying all completion criteria")

    return CompletionDecision(
        complete=complete,
        status=status,
        objective_coverage=round(coverage, 4),
        blocking_tasks=[str(x) for x in blocking_tasks if x],
        blocking_subquestions=[str(x) for x in blocking_subs if x],
        blocking_gaps=[str(x) for x in gaps],
        unresolved_conflicts=[str(x) for x in conflicts],
        validated_claims=validated,
        challenged_claims=challenged,
        rejected_claims=rejected,
        reasons=reasons or assessment.unresolved,
        satisfied_objectives=satisfied,
        unresolved_objectives=unresolved,
    )


def _all_subquestions(state: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dec in state.get("decompositions") or []:
        out.extend(dec.get("subquestions") or [])
    return out

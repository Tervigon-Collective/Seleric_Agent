"""Completion evaluator (pasted spec sec. 32).

    CompletionScore = 0.30*ObjectiveCoverage + 0.25*EvidenceCompleteness
                    + 0.20*ClaimValidation + 0.15*SkepticStatus
                    + 0.10*ContradictionResolution

    >= 0.90 -> finish   |   0.70-0.90 -> review gaps   |   < 0.70 -> continue

Pure function over mission state; no LLM. The coordinator calls this before
synthesis to decide whether the mission may stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WEIGHTS = {
    "objective_coverage": 0.30,
    "evidence_completeness": 0.25,
    "claim_validation": 0.20,
    "skeptic_status": 0.15,
    "contradiction_resolution": 0.10,
}

FINISH_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.70


@dataclass
class CompletionAssessment:
    score: float
    decision: str  # "finish" | "review" | "continue"
    components: dict[str, float] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)


def _objective_coverage(state: dict[str, Any], task_graph: dict[str, Any] | None) -> float:
    tasks = (task_graph or {}).get("tasks") or []
    dispatchable = [t for t in tasks if t.get("dispatchable")]
    tracked = [t for t in dispatchable if t.get("status") in {"done", "failed", "running"}]
    if tracked:
        done = [t for t in dispatchable if t.get("status") == "done"]
        return len(done) / len(dispatchable)
    # Advisory DAG (no live execution tracking yet) or no plan attached:
    # fall back to the mission outcome signal.
    if not state.get("claims"):
        return 0.0
    if state.get("status") == "completed":
        return 1.0
    if state.get("status") == "partial":
        return 0.6
    return 0.5


def _evidence_completeness(state: dict[str, Any]) -> float:
    evidence = state.get("evidence") or []
    if not evidence:
        return 0.0
    if state.get("error_code") == "INSUFFICIENT_EVIDENCE":
        return 0.5
    if state.get("status") == "partial":
        return 0.6
    return 1.0


def _claim_validation(state: dict[str, Any]) -> float:
    claims = state.get("claims") or []
    if not claims:
        return 0.0
    passed = [c for c in claims if c.get("gate_status") == "passed"]
    return len(passed) / len(claims)


def _skeptic_status(state: dict[str, Any]) -> float:
    findings = state.get("skeptic_findings") or []
    if not findings:
        return 1.0  # skeptic not required for V1 numeric lookups
    unresolved = [f for f in findings if f.get("status") not in {"resolved", "passed"}]
    return 0.0 if unresolved else 1.0


def _contradiction_resolution(state: dict[str, Any]) -> float:
    contradictions = state.get("contradictions") or []
    if not contradictions:
        return 1.0
    unresolved = [c for c in contradictions if not c.get("resolved")]
    return 0.0 if unresolved else 1.0


def assess_completion(
    state: dict[str, Any],
    task_graph: dict[str, Any] | None = None,
) -> CompletionAssessment:
    components = {
        "objective_coverage": _objective_coverage(state, task_graph),
        "evidence_completeness": _evidence_completeness(state),
        "claim_validation": _claim_validation(state),
        "skeptic_status": _skeptic_status(state),
        "contradiction_resolution": _contradiction_resolution(state),
    }
    score = round(sum(WEIGHTS[k] * v for k, v in components.items()), 4)

    if score >= FINISH_THRESHOLD:
        decision = "finish"
    elif score >= REVIEW_THRESHOLD:
        decision = "review"
    else:
        decision = "continue"

    unresolved: list[str] = []
    if components["evidence_completeness"] < 1.0:
        unresolved.append("Evidence is incomplete or partial for the requested scope")
    if components["claim_validation"] < 1.0:
        unresolved.append("One or more claims did not pass the provenance gate")
    if components["skeptic_status"] < 1.0:
        unresolved.append("Skeptic findings are unresolved")
    if components["contradiction_resolution"] < 1.0:
        unresolved.append("Contradictions between agents are unresolved")

    return CompletionAssessment(
        score=score, decision=decision, components=components, unresolved=unresolved
    )

"""Progressive problem decomposition — initial create, refine, validate, EIG."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from seleric_swarm.coordinator.contracts import (
    MissionObjective,
    NormalizedQuery,
    ProblemDecomposition,
    SubQuestion,
)
from seleric_swarm.coordinator.decomposition.templates import TEMPLATES, select_template
from seleric_swarm.coordinator.policies import CoordinatorPolicies, load_coordinator_policies


def _qid(mission_id: str, text: str) -> str:
    digest = hashlib.sha1(f"{mission_id}|{text.lower().strip()}".encode()).hexdigest()[:10]
    return f"SQ-{digest}"


def _dec_id(mission_id: str, version: int) -> str:
    return f"DEC-{mission_id[-8:]}-v{version}"


def _normalize_question_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def information_gain(
    *,
    priority: int,
    business_impact: float = 1.0,
    resolve_prob: float = 0.5,
    cost: float = 1.0,
    latency: float = 1.0,
) -> float:
    """Configurable EIG heuristic (ExpectedInformationGain × impact × P) / (cost+latency)."""
    eig = max(0.05, priority / 10.0)
    denom = max(0.1, cost + latency)
    return round((eig * business_impact * resolve_prob) / denom, 4)


def is_duplicate_subquestion(existing: list[SubQuestion], question: str) -> bool:
    norm = _normalize_question_text(question)
    for sq in existing:
        if sq.status in {"superseded", "irrelevant"}:
            continue
        if _normalize_question_text(sq.question) == norm:
            return True
        # near-duplicate: same purpose tokens
        if norm in _normalize_question_text(sq.question) or _normalize_question_text(sq.question) in norm:
            if abs(len(norm) - len(_normalize_question_text(sq.question))) < 12:
                return True
    return False


def initial_decomposition(
    *,
    mission_id: str,
    normalized: NormalizedQuery,
    policies: CoordinatorPolicies | None = None,
) -> ProblemDecomposition:
    policies = policies or load_coordinator_policies()
    template_name = select_template(normalized.intents, normalized.primary_metric, normalized.original_query)
    steps = TEMPLATES.get(template_name) or TEMPLATES["diagnostic"]
    dec_id = _dec_id(mission_id, 1)

    objectives = [
        MissionObjective(
            objective_id=f"O1-{mission_id[-6:]}",
            description=normalized.original_query,
            priority=10,
        )
    ]
    if "executive_health" in normalized.intents:
        objectives = [
            MissionObjective(objective_id="O1", description="Business performance", priority=9),
            MissionObjective(objective_id="O2", description="Paid acquisition", priority=8),
            MissionObjective(objective_id="O3", description="Funnel health", priority=7),
            MissionObjective(objective_id="O4", description="Profitability", priority=6),
            MissionObjective(objective_id="O5", description="Operational risk", priority=5),
        ]

    subquestions: list[SubQuestion] = []
    for step in steps:
        qtext = step["question"]
        if normalized.primary_metric and "{metric}" not in qtext and template_name == "lookup":
            qtext = qtext.replace("the metric", normalized.primary_metric)
        sq = SubQuestion(
            question_id=_qid(mission_id, qtext),
            mission_id=mission_id,
            decomposition_id=dec_id,
            question=qtext,
            purpose=str(step["purpose"]),
            required_capabilities=_caps_for_purpose(str(step["purpose"])),
            required_artifact_types=_artifacts_for_purpose(str(step["purpose"])),
            priority=int(step.get("priority") or 5),
            expected_information_gain=information_gain(priority=int(step.get("priority") or 5)),
            status="ready" if int(step.get("priority") or 5) >= 7 else "pending",
            branch=step.get("branch"),
        )
        if not is_duplicate_subquestion(subquestions, sq.question):
            subquestions.append(sq)

    return ProblemDecomposition(
        decomposition_id=dec_id,
        mission_id=mission_id,
        root_question=normalized.original_query,
        version=1,
        objectives=objectives,
        subquestions=subquestions,
        candidate_domains=list(normalized.candidate_domains),
        status="active",
        questions_added=[s.question_id for s in subquestions],
        template=template_name,
    )


def _caps_for_purpose(purpose: str) -> list[str]:
    mapping = {
        "retrieve": ["metric_observation", "evidence_collection"],
        "detect_anomaly": ["anomaly_analysis"],
        "generate_hypotheses": ["hypothesis_generation"],
        "causal_validation": ["causal_diagnosis"],
        "infer": ["forecasting"],
        "generate_interventions": ["intervention_design"],
        "skeptic_validation": ["challenge"],
        "test_hypotheses": ["hypothesis_test"],
    }
    return list(mapping.get(purpose) or ["metric_observation"])


def _artifacts_for_purpose(purpose: str) -> list[str]:
    mapping = {
        "retrieve": ["evidence"],
        "detect_anomaly": ["anomaly"],
        "generate_hypotheses": ["hypothesis"],
        "causal_validation": ["causal"],
        "infer": ["prediction"],
        "generate_interventions": ["strategy"],
        "skeptic_validation": ["skeptic"],
    }
    return list(mapping.get(purpose) or ["evidence"])


def refine_from_evidence(
    current: ProblemDecomposition,
    *,
    evidence: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    reason: str,
    policies: CoordinatorPolicies | None = None,
) -> ProblemDecomposition:
    """Create a new decomposition version when the causal frontier moves."""
    policies = policies or load_coordinator_policies()
    if current.version >= policies.decomposition.max_versions:
        return current

    retired: list[str] = []
    added: list[SubQuestion] = []
    # Deep-copy-ish status updates on a shallow list of models
    kept = [sq.model_copy(deep=True) for sq in current.subquestions]

    # Rule out stable media drivers when conversion is the frontier.
    media_stable = _media_drivers_stable(evidence, anomalies)
    conversion_abnormal = _conversion_abnormal(evidence, anomalies)
    mobile_abnormal = _mobile_cvr_abnormal(evidence, anomalies)

    # Prefer stepwise refinement: conversion/funnel first; mobile/technical next.
    already_has_funnel = any(sq.branch == "funnel" for sq in kept)
    already_has_technical = any(sq.branch == "technical" for sq in kept)

    if media_stable and conversion_abnormal and not already_has_funnel:
        for sq in kept:
            if sq.branch == "media" and sq.status not in {"answered", "irrelevant", "superseded"}:
                sq.status = "irrelevant"
                retired.append(sq.question_id)
        funnel_qs = [
            ("Which funnel stage deteriorated?", "funnel_stage", "funnel", 9),
            ("Did sessions change abnormally?", "sessions", "funnel", 6),
            ("Did PDP→ATC deteriorate?", "pdp_atc", "funnel", 6),
            ("Did checkout deteriorate?", "checkout", "funnel", 7),
            ("Did purchase CVR deteriorate?", "purchase", "funnel", 8),
        ]
        for qtext, purpose, branch, pri in funnel_qs:
            if is_duplicate_subquestion(kept + added, qtext):
                continue
            added.append(
                SubQuestion(
                    question_id=_qid(current.mission_id, qtext),
                    mission_id=current.mission_id,
                    decomposition_id="",
                    question=qtext,
                    purpose=purpose,
                    required_capabilities=["metric_observation", "anomaly_analysis"],
                    required_artifact_types=["evidence", "anomaly"],
                    priority=pri,
                    expected_information_gain=information_gain(priority=pri, business_impact=1.2),
                    status="ready",
                    branch=branch,
                )
            )

    elif mobile_abnormal and already_has_funnel and not already_has_technical:
        tech_qs = [
            ("Why did mobile purchase CVR fall?", "mobile_why", "technical", 9),
            ("Did mobile LCP regress?", "latency", "technical", 8),
            ("Did JS error rate spike?", "js_errors", "technical", 8),
            ("Was there a frontend deployment near onset?", "deployment", "technical", 7),
            ("Is desktop purchase CVR stable (control)?", "desktop_control", "technical", 6),
        ]
        for qtext, purpose, branch, pri in tech_qs:
            if is_duplicate_subquestion(kept + added, qtext):
                continue
            added.append(
                SubQuestion(
                    question_id=_qid(current.mission_id, qtext),
                    mission_id=current.mission_id,
                    decomposition_id="",
                    question=qtext,
                    purpose=purpose,
                    required_capabilities=["metric_observation", "anomaly_analysis"],
                    required_artifact_types=["evidence", "anomaly"],
                    priority=pri,
                    expected_information_gain=information_gain(priority=pri, business_impact=1.4),
                    status="ready",
                    branch=branch,
                )
            )

    if not added and not retired:
        return current

    version = current.version + 1
    dec_id = _dec_id(current.mission_id, version)
    for sq in added:
        sq.decomposition_id = dec_id
    new_subs = kept + added
    return ProblemDecomposition(
        decomposition_id=dec_id,
        mission_id=current.mission_id,
        root_question=current.root_question,
        parent_decomposition_id=current.decomposition_id,
        version=version,
        objectives=list(current.objectives),
        subquestions=new_subs,
        assumptions=list(current.assumptions),
        required_evidence=list(current.required_evidence),
        candidate_domains=_domains_from_subs(new_subs, current.candidate_domains),
        created_from_evidence_refs=[e.get("artifact_id") or e.get("evidence_id") or "" for e in evidence if e][:20],
        reason_for_revision=reason,
        status="active",
        questions_added=[s.question_id for s in added],
        questions_retired=retired,
        template=current.template,
    )


def refine_from_skeptic_followups(
    current: ProblemDecomposition,
    followups: list[dict[str, Any]],
) -> ProblemDecomposition:
    """Skeptic REVISE may extend decomposition with targeted subquestions only."""
    added: list[SubQuestion] = []
    for f in followups:
        qtext = str(f.get("question") or f.get("objective") or "").strip()
        if not qtext:
            continue
        if is_duplicate_subquestion(current.subquestions + added, qtext):
            continue
        added.append(
            SubQuestion(
                question_id=_qid(current.mission_id, qtext),
                mission_id=current.mission_id,
                decomposition_id="",
                question=qtext,
                purpose="skeptic_followup",
                required_capabilities=[str(f.get("requested_capability") or "metric_observation")],
                priority=int(f.get("priority") or 7),
                expected_information_gain=information_gain(priority=int(f.get("priority") or 7)),
                status="ready",
                branch=f.get("preferred_domain"),
                metadata={"from_followup": f.get("task_id")},
            )
        )
    if not added:
        return current
    version = current.version + 1
    dec_id = _dec_id(current.mission_id, version)
    for sq in added:
        sq.decomposition_id = dec_id
    return ProblemDecomposition(
        decomposition_id=dec_id,
        mission_id=current.mission_id,
        root_question=current.root_question,
        parent_decomposition_id=current.decomposition_id,
        version=version,
        objectives=list(current.objectives),
        subquestions=list(current.subquestions) + added,
        candidate_domains=list(current.candidate_domains),
        reason_for_revision="skeptic_followups",
        created_from_evidence_refs=list(current.created_from_evidence_refs),
        status="active",
        questions_added=[s.question_id for s in added],
        questions_retired=[],
        template=current.template,
    )


def select_next_subquestions(
    decomposition: ProblemDecomposition,
    *,
    limit: int = 3,
    eig_threshold: float = 0.12,
) -> list[SubQuestion]:
    open_qs = [
        sq
        for sq in decomposition.subquestions
        if sq.status in {"pending", "ready"}
        and (sq.expected_information_gain or 0) >= eig_threshold
    ]
    open_qs.sort(key=lambda s: (-(s.expected_information_gain or 0), -s.priority, s.question_id))
    return open_qs[:limit]


def _metric_deviation(items: list[dict[str, Any]], metric_substr: str) -> float | None:
    for a in items:
        mid = str(a.get("metric_id") or a.get("metric_or_fact") or "")
        if metric_substr in mid:
            if a.get("deviation_pct") is not None:
                return float(a["deviation_pct"])
            if a.get("change_pct") is not None:
                return float(a["change_pct"])
    return None


def _media_drivers_stable(evidence: list[dict[str, Any]], anomalies: list[dict[str, Any]]) -> bool:
    pool = anomalies + evidence
    stable = True
    found_any = False
    for key in ("cpm", "ctr", "cpc"):
        dev = _metric_deviation(pool, key)
        if dev is None:
            continue
        found_any = True
        if abs(dev) >= 10:
            stable = False
    return found_any and stable


def _conversion_abnormal(evidence: list[dict[str, Any]], anomalies: list[dict[str, Any]]) -> bool:
    pool = anomalies + evidence
    for key in ("purchase_cvr", "cvr", "conversion"):
        dev = _metric_deviation(pool, key)
        if dev is not None and abs(dev) >= 10:
            return True
    return False


def _mobile_cvr_abnormal(evidence: list[dict[str, Any]], anomalies: list[dict[str, Any]]) -> bool:
    pool = anomalies + evidence
    for a in pool:
        dims = a.get("dimensions") or {}
        mid = str(a.get("metric_id") or a.get("metric_or_fact") or "")
        if dims.get("device") == "mobile" or "mobile" in mid:
            dev = a.get("deviation_pct")
            if dev is None:
                dev = a.get("change_pct")
            if dev is not None and abs(float(dev)) >= 10:
                return True
    return False


def _domains_from_subs(subs: list[SubQuestion], fallback: list[str]) -> list[str]:
    domains = list(fallback)
    for sq in subs:
        if sq.branch == "technical" and "technical" not in domains:
            domains.append("technical")
        if sq.branch == "funnel" and "funnel" not in domains:
            domains.append("funnel")
    return domains

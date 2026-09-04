"""LangGraph state for the Skeptic workflow.

Every node returns a partial ``SkepticState``; LangGraph shallow-merges it. Rich
objects (the resolved :class:`SkepticContext`, ``ValidatorOutcome`` list) are
carried under ``_context`` / ``_outcomes`` and are not serialized into a
checkpoint payload verbatim -- the durable record is the final ``verdict`` dict
plus ``audit``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class SkepticState(TypedDict, total=False):
    # inputs
    mission_id: str
    skeptic_run_id: str
    request: dict[str, Any]

    # intake
    claim: dict[str, Any]
    claim_type: str
    risk_score: float
    risk_class: str
    risk_components: dict[str, float]

    # planning / evidence
    evidence_refs: list[str]
    loaded_evidence: list[dict[str, Any]]
    challenge_plan: list[str]
    validators_selected: list[str]

    # findings
    contradictions: list[dict[str, Any]]
    alternative_hypotheses: list[dict[str, Any]]
    validator_results: dict[str, dict[str, Any]]
    evidence_gaps: list[dict[str, Any]]
    methodological_issues: list[str]
    required_followups: list[dict[str, Any]]

    # scoring / outcome
    trust_score: float
    trust_label: str
    trust_components: dict[str, float]
    verdict: str
    limitations: list[str]
    explanation: str
    status: str  # running | done | error
    error: str

    # non-serialized carriers (LangGraph passes objects through fine in-process)
    _context: Any
    _outcomes: list[Any]
    _verdict_model: Any
    _t0: float

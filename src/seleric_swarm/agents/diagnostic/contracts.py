"""Typed contracts for the Diagnostic subsystem.

The Diagnostic Agent answers "why did it change?" and emits artifacts the Skeptic
already knows how to validate:

    DiagnosticArtifact        (re-exported from agents.skeptic.contracts)
    CausalAnalysisArtifact    (re-exported from agents.skeptic.contracts)
    Claim[]                   (claim_type="causal", causal_refs=[...])

Its internal richer models (``DiagnosticHypothesis``, ``HypothesisTest``,
``TestResult``, ``DiagnosticFinding``) live here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# Re-export the Skeptic-facing output contracts so downstream code has one import.
from seleric_swarm.agents.skeptic.contracts import (
    CausalAnalysisArtifact,
    Claim,
    DiagnosticArtifact,
)

HypothesisStatus = Literal["proposed", "testing", "retained", "rejected", "inconclusive"]

TestKind = Literal[
    "evidence_sufficiency",
    "temporal_precedence",
    "segment_specificity",
    "dose_response",
    "control_divergence",
    "mechanism_consistency",
]

CausalConfidence = Literal[
    "ASSOCIATION_ONLY",
    "PLAUSIBLE_CAUSAL",
    "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS",
    "STRONGLY_SUPPORTED",
    "REJECTED",
]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _rid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #


class DiagnosticRequest(BaseModel):
    mission_id: str
    question: str
    primary_metric: str = ""
    outcome_metric: str = ""
    anomaly_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    lead_domain: str | None = None
    time_range: dict[str, Any] = Field(default_factory=dict)
    degradation_started_at: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    # optional pandas DataFrame of observations for real DoWhy estimation
    observations: Any = None

    model_config = {"arbitrary_types_allowed": True}


# --------------------------------------------------------------------------- #
# Internal models
# --------------------------------------------------------------------------- #


class HypothesisTest(BaseModel):
    test_id: str = Field(default_factory=lambda: _rid("HT"))
    kind: TestKind
    description: str
    hypothesis_id: str
    required_capability: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class TestResult(BaseModel):
    test_id: str
    hypothesis_id: str
    kind: TestKind
    passed: bool
    hard_gate: bool = False           # a failed hard gate rejects the hypothesis outright
    detail: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class DiagnosticHypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: _rid("HYP"))
    statement: str
    mechanism: str = ""
    treatment_metric: str = ""
    outcome_metric: str = ""
    domains: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)
    prior_score: float = 0.0
    posterior_score: float = 0.0
    status: HypothesisStatus = "proposed"
    is_primary: bool = False
    llm_generated: bool = False
    synthetic: bool = False
    test_results: list[TestResult] = Field(default_factory=list)
    rejection_reason: str | None = None


class DiagnosticFinding(BaseModel):
    """The retained root-cause candidate + how strongly it is supported."""

    finding_id: str = Field(default_factory=lambda: _rid("DFIND"))
    statement: str
    mechanism: str
    causal_confidence: CausalConfidence
    causal_ref: str | None = None
    retained_hypothesis_id: str | None = None
    supporting_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    ruled_out: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DiagnosticResult(BaseModel):
    """Full return value of ``DiagnosticAgent.diagnose``."""

    diagnostic_run_id: str = Field(default_factory=lambda: _rid("DIAG"))
    mission_id: str
    question: str
    outcome_metric: str
    hypotheses: list[DiagnosticHypothesis] = Field(default_factory=list)
    finding: DiagnosticFinding | None = None
    diagnostic_artifact: DiagnosticArtifact | None = None
    causal_artifact: CausalAnalysisArtifact | None = None
    claims: list[Claim] = Field(default_factory=list)
    methodology: str = ""
    limitations: list[str] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)
    synthetic: bool = False
    created_at: str = Field(default_factory=_now)

    def retained(self) -> list[DiagnosticHypothesis]:
        return [h for h in self.hypotheses if h.status == "retained"]

    def rejected(self) -> list[DiagnosticHypothesis]:
        return [h for h in self.hypotheses if h.status == "rejected"]

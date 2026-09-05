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


ContradictionCategory = Literal[
    "temporal", "segment", "metric", "source", "causal", "statistical", "business_logic"
]
ContradictionSeverity = Literal["info", "warning", "blocking"]

_CATEGORY_BY_TEST_KIND: dict[str, ContradictionCategory] = {
    "temporal_precedence": "temporal",
    "segment_specificity": "segment",
    "control_divergence": "segment",
    "evidence_sufficiency": "metric",
    "dose_response": "statistical",
    "mechanism_consistency": "statistical",
}


class DiagnosticContradiction(BaseModel):
    """Evidence that does not fit a hypothesis (spec's falsification bookkeeping).

    Derived from a failed ``TestResult``, not a separate analysis pass — the
    test-driven rejection logic already *decides* on this evidence; this model
    just makes that reasoning inspectable instead of only affecting a score.
    """

    contradiction_id: str = Field(default_factory=lambda: _rid("CTR"))
    hypothesis_id: str
    category: ContradictionCategory
    description: str
    evidence_refs: list[str] = Field(default_factory=list)
    severity: ContradictionSeverity = "warning"

    @classmethod
    def from_test_result(
        cls, result: TestResult, *, evidence_refs: list[str] | None = None
    ) -> DiagnosticContradiction:
        return cls(
            hypothesis_id=result.hypothesis_id,
            category=_CATEGORY_BY_TEST_KIND.get(result.kind, "statistical"),
            description=result.note or f"{result.kind} failed",
            evidence_refs=list(evidence_refs or []),
            severity="blocking" if result.hard_gate else "warning",
        )


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


FindingRole = Literal["primary", "secondary", "contributor"]


class DiagnosticFinding(BaseModel):
    """A supported (or inconclusive-but-reported) root-cause candidate.

    Multiple findings may co-exist on one ``DiagnosticResult`` (spec §54-55):
    a causal factor can be real without being the *whole* explanation. ``role``
    ranks them; it is never a claim that unranked findings are false, only that
    they explain less of the observed change than ``role="primary"``.
    """

    finding_id: str = Field(default_factory=lambda: _rid("DFIND"))
    statement: str
    mechanism: str
    causal_confidence: CausalConfidence
    causal_ref: str | None = None
    retained_hypothesis_id: str | None = None
    role: FindingRole = "primary"
    estimated_effect: float | None = None
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
    # All supported/inconclusive-but-reported findings, ranked by role. ``finding``
    # is kept as a convenience alias for ``findings[0]`` (backward compatible with
    # single-cause callers); prefer ``findings`` when multiple contributors matter.
    findings: list[DiagnosticFinding] = Field(default_factory=list)
    diagnostic_artifact: DiagnosticArtifact | None = None
    causal_artifact: CausalAnalysisArtifact | None = None
    claims: list[Claim] = Field(default_factory=list)
    methodology: str = ""
    limitations: list[str] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)
    synthetic: bool = False
    created_at: str = Field(default_factory=_now)
    contradictions: list[DiagnosticContradiction] = Field(default_factory=list)

    # -- leadership / decomposition advice (Coordinator decides, Diagnostic only proposes) --
    recommended_domain_lead: str | None = None
    leadership_transfer_recommended: bool = False
    leadership_transfer_reason: str | None = None

    # -- coarse routing label for downstream Prediction/Strategy, not a causal claim --
    incident_type: str | None = None

    # -- residual uncertainty (spec §54, §123): what the retained findings do
    # NOT explain. Only ever a qualitative flag; never a fabricated percentage.
    residual_unexplained: bool = False

    def retained(self) -> list[DiagnosticHypothesis]:
        return [h for h in self.hypotheses if h.status == "retained"]

    def rejected(self) -> list[DiagnosticHypothesis]:
        return [h for h in self.hypotheses if h.status == "rejected"]

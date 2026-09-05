"""Typed contracts for the Skeptic verification subsystem.

These models are the Skeptic's *stable* interface. Upstream agents (Observer,
Anomaly, and the future Diagnostic / Prediction / Strategy agents) target the
``*Artifact`` shapes here; the Skeptic emits :class:`SkepticVerdict`.

The existing swarm Blackboard (``seleric_swarm.swarm.artifacts``) uses a leaner
internal dict shape. Every artifact contract below provides ``from_blackboard``
so a live swarm run can be validated without upstream code changes, while a
future agent can also construct the richer contract directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Enums / literals
# --------------------------------------------------------------------------- #

ClaimType = Literal[
    "numeric",
    "comparison",
    "anomaly",
    "correlation",
    "causal",
    "forecast",
    "recommendation",
    "action",
    "qualitative",
]

Verdict = Literal["PASS", "REVISE", "REJECT"]

TrustLabel = Literal["VERIFIED", "STRONG", "PROBABLE", "WEAK", "INSUFFICIENT"]

ChallengeCategory = Literal[
    "evidence",
    "provenance",
    "metric",
    "statistical",
    "anomaly",
    "causal",
    "model",
    "forecast",
    "contradiction",
    "alternative_hypothesis",
    "strategy",
    "data_quality",
    "temporal",
    "source",
]

Severity = Literal["info", "warning", "blocking"]

ContradictionType = Literal[
    "data_contradiction",
    "metric_semantic_conflict",
    "time_range_conflict",
    "source_conflict",
    "methodology_conflict",
    "causal_conflict",
    "model_conflict",
    "factual_conflict",
]

CausalConfidence = Literal[
    "ASSOCIATION_ONLY",
    "PLAUSIBLE_CAUSAL",
    "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS",
    "STRONGLY_SUPPORTED",
    "REJECTED",
]

ModelVerdict = Literal[
    "MODEL_VALID",
    "MODEL_DEGRADED",
    "MODEL_OUT_OF_DOMAIN",
    "MODEL_DRIFTED",
    "MODEL_METADATA_INCOMPLETE",
    "MODEL_REJECTED",
]

AnomalyVerdict = Literal[
    "SUPPORTED_ANOMALY",
    "WEAK_ANOMALY",
    "NOT_ENOUGH_DATA",
    "INVALID_DETECTOR_FOR_CONTEXT",
    "REJECTED_ANOMALY",
]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _rid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Claim
# --------------------------------------------------------------------------- #


class Claim(BaseModel):
    """A candidate conclusion produced by another agent, submitted for review."""

    model_config = ConfigDict(protected_namespaces=())

    claim_id: str = Field(default_factory=lambda: _rid("CL"))
    mission_id: str
    claim_type: ClaimType
    statement: str
    origin_agent: str

    support_refs: list[str] = Field(default_factory=list)
    contradiction_refs: list[str] = Field(default_factory=list)

    metric_refs: list[str] = Field(default_factory=list)
    anomaly_refs: list[str] = Field(default_factory=list)
    causal_refs: list[str] = Field(default_factory=list)
    diagnostic_refs: list[str] = Field(default_factory=list)
    model_refs: list[str] = Field(default_factory=list)
    forecast_refs: list[str] = Field(default_factory=list)
    strategy_refs: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Upstream artifact contracts (what the Skeptic validates)
# --------------------------------------------------------------------------- #


class EvidenceArtifact(BaseModel):
    evidence_id: str
    mission_id: str = ""
    source: str = ""
    metric_id: str | None = None
    fact_type: str | None = None
    value: Any = None
    baseline: Any = None
    change_pct: float | None = None
    unit: str | None = None
    time_range: dict[str, Any] = Field(default_factory=dict)
    timezone: str | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: str | None = None
    freshness: str | None = None
    query_hash: str | None = None
    source_version: str | None = None
    calculation_version: str | None = None
    sample_size: int | None = None
    quality_flags: list[str] = Field(default_factory=list)
    synthetic: bool = False
    data_origin: str = "DERIVED"

    @classmethod
    def from_blackboard(cls, payload: dict[str, Any]) -> EvidenceArtifact:
        prov = payload.get("provenance") or {}
        metric = payload.get("metric_or_fact") or payload.get("metric_id")
        is_fact = bool(metric) and str(metric).startswith("event.")

        def pick(*keys: str) -> Any:
            for key in keys:
                if payload.get(key) is not None:
                    return payload[key]
                if prov.get(key) is not None:
                    return prov[key]
            return None

        return cls(
            evidence_id=payload.get("artifact_id") or payload.get("evidence_id") or _rid("EV"),
            mission_id=payload.get("mission_id", ""),
            source=payload.get("source", ""),
            metric_id=None if is_fact else metric,
            fact_type=metric if is_fact else payload.get("fact_type"),
            value=payload.get("value"),
            baseline=payload.get("baseline"),
            change_pct=payload.get("change_pct"),
            unit=payload.get("unit"),
            time_range=payload.get("time_range") or {},
            timezone=(payload.get("time_range") or {}).get("timezone") or pick("timezone"),
            dimensions=payload.get("dimensions") or {},
            retrieved_at=payload.get("retrieved_at") or payload.get("created_at"),
            freshness=payload.get("freshness"),
            query_hash=pick("query_hash", "tool_call_hash"),
            source_version=pick("source_version"),
            calculation_version=pick("calculation_version"),
            sample_size=pick("sample_size"),
            quality_flags=payload.get("quality_flags") or [],
            synthetic=bool(payload.get("synthetic")),
            data_origin=payload.get("data_origin", "DERIVED"),
        )


class AnomalyArtifact(BaseModel):
    anomaly_id: str
    mission_id: str = ""
    metric_id: str
    observed: float | None = None
    expected: float | None = None
    expected_range: list[float] = Field(default_factory=list)
    deviation_pct: float | None = None
    anomaly_score: float | None = None
    detector_id: str | None = None
    detector_version: str | None = None
    analysis_window: dict[str, Any] = Field(default_factory=dict)
    dimensions: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    sample_size: int | None = None
    history_days: int | None = None
    seasonality_handled: bool | None = None
    synthetic: bool = False

    @classmethod
    def from_blackboard(cls, payload: dict[str, Any]) -> AnomalyArtifact:
        detector = payload.get("detector") or {}
        rng = payload.get("expected_range") or []
        return cls(
            anomaly_id=payload.get("artifact_id") or payload.get("anomaly_id") or _rid("AN"),
            mission_id=payload.get("mission_id", ""),
            metric_id=payload.get("metric_id", ""),
            observed=payload.get("observed"),
            expected=(sum(rng) / len(rng)) if rng else payload.get("expected"),
            expected_range=list(rng),
            deviation_pct=payload.get("deviation_pct"),
            anomaly_score=payload.get("score") if payload.get("score") is not None else payload.get("anomaly_score"),
            detector_id=detector.get("id") or payload.get("detector_id"),
            detector_version=detector.get("version") or payload.get("detector_version"),
            analysis_window=payload.get("analysis_window") or {},
            dimensions=payload.get("dimensions") or {},
            evidence_refs=payload.get("evidence_refs") or [],
            sample_size=payload.get("sample_size") or detector.get("sample_size"),
            history_days=payload.get("history_days") or detector.get("history_days"),
            seasonality_handled=payload.get("seasonality_handled"),
            synthetic=bool(payload.get("synthetic")),
        )


class CausalAnalysisArtifact(BaseModel):
    causal_id: str
    mission_id: str = ""
    treatment: str = ""
    outcome: str = ""
    graph_id: str = ""
    graph_version: str | None = None
    common_causes: list[str] = Field(default_factory=list)
    effect_modifiers: list[str] = Field(default_factory=list)
    estimator: str = ""
    estimator_parameters: dict[str, Any] = Field(default_factory=dict)
    estimated_effect: float | None = None
    confidence_interval: list[float] = Field(default_factory=list)
    sample_size: int | None = None
    refutation_results: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    treatment_started_at: str | None = None
    outcome_started_at: str | None = None
    passed: bool = False
    synthetic: bool = False

    @classmethod
    def from_blackboard(cls, payload: dict[str, Any]) -> CausalAnalysisArtifact:
        return cls(
            causal_id=payload.get("artifact_id") or payload.get("causal_id") or _rid("CAUS"),
            mission_id=payload.get("mission_id", ""),
            treatment=payload.get("treatment", ""),
            outcome=payload.get("outcome", ""),
            graph_id=payload.get("graph_id", ""),
            graph_version=payload.get("graph_version"),
            common_causes=payload.get("common_causes") or [],
            effect_modifiers=payload.get("effect_modifiers") or [],
            estimator=payload.get("estimator", ""),
            estimator_parameters=payload.get("estimator_parameters") or {},
            estimated_effect=payload.get("effect") if payload.get("effect") is not None else payload.get("estimated_effect"),
            confidence_interval=payload.get("effect_ci") or payload.get("confidence_interval") or [],
            sample_size=payload.get("sample_size"),
            refutation_results=payload.get("refutations") or payload.get("refutation_results") or [],
            assumptions=payload.get("assumptions") or [],
            limitations=payload.get("limitations") or [],
            evidence_refs=payload.get("evidence_refs") or [],
            treatment_started_at=payload.get("treatment_started_at"),
            outcome_started_at=payload.get("outcome_started_at"),
            passed=bool(payload.get("passed")),
            synthetic=bool(payload.get("synthetic")),
        )


class DiagnosticArtifact(BaseModel):
    """Future Diagnostic Agent contract. No current implementation is required."""

    diagnostic_id: str
    mission_id: str = ""
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    retained_hypotheses: list[str] = Field(default_factory=list)
    rejected_hypotheses: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    methodology: str = ""
    limitations: list[str] = Field(default_factory=list)
    causal_ref: str | None = None
    synthetic: bool = False


class ForecastArtifact(BaseModel):
    """Future Prediction Agent contract."""

    model_config = ConfigDict(protected_namespaces=())

    forecast_id: str
    mission_id: str = ""
    target_metric: str = ""
    prediction: Any = None
    interval: list[float] = Field(default_factory=list)
    horizon: str = ""
    model_id: str | None = None
    model_version: str | None = None
    feature_set_id: str | None = None
    feature_set_version: str | None = None
    training_window: dict[str, Any] = Field(default_factory=dict)
    backtest_metrics: dict[str, Any] = Field(default_factory=dict)
    drift_status: str | None = None
    applicability_status: str | None = None
    generated_at: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    llm_generated: bool = False
    synthetic: bool = False

    @classmethod
    def from_blackboard(cls, payload: dict[str, Any]) -> ForecastArtifact:
        model = payload.get("model") or {}
        return cls(
            forecast_id=payload.get("artifact_id") or payload.get("forecast_id") or _rid("PRED"),
            mission_id=payload.get("mission_id", ""),
            target_metric=payload.get("target") or payload.get("target_metric", ""),
            prediction=payload.get("prediction"),
            interval=payload.get("interval") or [],
            horizon=payload.get("horizon", ""),
            model_id=model.get("id") or payload.get("model_id"),
            model_version=model.get("version") or payload.get("model_version"),
            feature_set_id=model.get("feature_set") or payload.get("feature_set_id"),
            feature_set_version=payload.get("feature_set_version"),
            training_window=payload.get("training_window") or {},
            backtest_metrics=payload.get("backtest_metrics") or (
                {"reported": model.get("backtest_metric")} if model.get("backtest_metric") else {}
            ),
            drift_status=payload.get("drift_status") or model.get("drift_status"),
            applicability_status=payload.get("applicability_status"),
            generated_at=payload.get("generated_at") or payload.get("created_at"),
            evidence_refs=payload.get("evidence_refs") or [],
            limitations=payload.get("limitations") or [],
            llm_generated=bool(payload.get("llm_generated")),
            synthetic=bool(payload.get("synthetic")),
        )


# Prediction artifact is an alias shape kept explicit for the future agent.
PredictionArtifact = ForecastArtifact


class StrategyArtifact(BaseModel):
    strategy_id: str
    mission_id: str = ""
    action: str = ""
    mechanism_ref: str | None = None
    expected_effect: str | dict[str, Any] | None = None
    cost: str | float | None = None
    risk: str | None = None
    reversibility: str | None = None
    owner_domain: str | None = None
    constraints: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    measurement_plan: str = ""
    support_refs: list[str] = Field(default_factory=list)
    options: list[dict[str, Any]] = Field(default_factory=list)
    synthetic: bool = False

    @classmethod
    def from_blackboard(cls, payload: dict[str, Any]) -> StrategyArtifact:
        options = payload.get("options") or []
        recommended = payload.get("recommended") or []
        top = next((o for o in options if o.get("action") in recommended), options[0] if options else {})
        return cls(
            strategy_id=payload.get("artifact_id") or payload.get("strategy_id") or _rid("STRAT"),
            mission_id=payload.get("mission_id", ""),
            action=top.get("action") or (recommended[0] if recommended else payload.get("action", "")),
            mechanism_ref=payload.get("problem_ref") or payload.get("mechanism_ref"),
            expected_effect=top.get("expected_impact") or payload.get("expected_effect"),
            cost=top.get("cost") or payload.get("cost"),
            risk=top.get("risk") or payload.get("risk"),
            reversibility=top.get("reversibility") or payload.get("reversibility"),
            owner_domain=payload.get("owner_domain"),
            constraints=payload.get("constraints") or [],
            prerequisites=payload.get("prerequisites") or [],
            measurement_plan=payload.get("measurement_plan", ""),
            support_refs=payload.get("evidence_refs") or payload.get("support_refs") or [],
            options=options,
            synthetic=bool(payload.get("synthetic")),
        )


# --------------------------------------------------------------------------- #
# Skeptic output contracts
# --------------------------------------------------------------------------- #


class Challenge(BaseModel):
    challenge_id: str = Field(default_factory=lambda: _rid("CH"))
    category: ChallengeCategory
    severity: Severity
    description: str
    evidence_refs: list[str] = Field(default_factory=list)
    remediation_hint: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class EvidenceGap(BaseModel):
    gap_id: str = Field(default_factory=lambda: _rid("GAP"))
    description: str
    reason_required: str
    capability_required: str | None = None
    blocking: bool = False
    priority: int = 5


class FollowUpTask(BaseModel):
    task_id: str = Field(default_factory=lambda: _rid("FUP"))
    requested_capability: str
    objective: str
    question: str
    evidence_refs: list[str] = Field(default_factory=list)
    priority: int = 5
    blocking: bool = False
    preferred_domain: str | None = None


class AlternativeHypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: _rid("ALT"))
    hypothesis: str
    mechanism: str = ""
    supporting_observations: list[str] = Field(default_factory=list)
    contradictory_observations: list[str] = Field(default_factory=list)
    falsification_test: str = ""
    priority: int = 5
    status: Literal["open", "supported", "eliminated"] = "open"


class SkepticVerdict(BaseModel):
    skeptic_run_id: str = Field(default_factory=lambda: _rid("SK"))
    mission_id: str
    claim_id: str
    claim_type: ClaimType

    verdict: Verdict
    trust_score: float
    trust_label: TrustLabel

    challenges: list[Challenge] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    alternative_hypotheses: list[AlternativeHypothesis] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    methodological_issues: list[str] = Field(default_factory=list)
    required_followups: list[FollowUpTask] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    validator_results: dict[str, Any] = Field(default_factory=dict)

    risk_score: float = 0.0
    risk_class: str = "R0"
    trust_components: dict[str, float] = Field(default_factory=dict)
    explanation: str = ""
    audit: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class SkepticValidationRequest(BaseModel):
    mission_id: str
    claim: Claim
    evidence_refs: list[str] = Field(default_factory=list)
    risk_context: dict[str, Any] = Field(default_factory=dict)
    available_artifact_refs: list[str] = Field(default_factory=list)
    blind_review: bool | None = None

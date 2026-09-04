"""Shared runtime context + validator result record.

``SkepticContext`` is assembled once by the agent (claim + resolved artifacts +
injected collaborators + policies) and passed read-only to every validator,
planner and scorer. ``ValidatorOutcome`` is the uniform shape each validator
returns so the router, trust scorer and verdict engine stay generic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.agents.skeptic.contracts import (
    AnomalyArtifact,
    CausalAnalysisArtifact,
    Challenge,
    Claim,
    DiagnosticArtifact,
    EvidenceArtifact,
    EvidenceGap,
    FollowUpTask,
    ForecastArtifact,
    StrategyArtifact,
)
from seleric_swarm.agents.skeptic.policies import SkepticPolicies
from seleric_swarm.agents.skeptic.reasoning import NullReasoningModel, ReasoningModel
from seleric_swarm.agents.skeptic.registries import (
    BasicCausalValidationService,
    BusinessRuleService,
    CausalGraphRegistry,
    CausalValidationService,
    DeterministicStatsValidator,
    DriftMonitor,
    IncidentRegistry,
    InMemoryBusinessRuleService,
    InMemoryCausalGraphRegistry,
    InMemoryIncidentRegistry,
    InMemoryMetricSemanticsRegistry,
    InMemoryModelRegistry,
    MetricSemanticsRegistry,
    ModelRegistry,
    NullDriftMonitor,
    StatisticalValidatorService,
)


@dataclass(frozen=True)
class SkepticDeps:
    """Injected collaborators. All optional; sane in-memory defaults are used."""

    metric_registry: MetricSemanticsRegistry = field(default_factory=InMemoryMetricSemanticsRegistry)
    model_registry: ModelRegistry = field(default_factory=InMemoryModelRegistry)
    causal_graphs: CausalGraphRegistry = field(default_factory=InMemoryCausalGraphRegistry)
    incident_registry: IncidentRegistry = field(default_factory=InMemoryIncidentRegistry)
    rules: BusinessRuleService = field(default_factory=InMemoryBusinessRuleService)
    stats: StatisticalValidatorService = field(default_factory=DeterministicStatsValidator)
    drift_monitor: DriftMonitor = field(default_factory=NullDriftMonitor)
    causal_service: CausalValidationService | None = None
    reasoning: ReasoningModel = field(default_factory=NullReasoningModel)

    def resolved_causal_service(self) -> CausalValidationService:
        return self.causal_service or BasicCausalValidationService(self.causal_graphs)


@dataclass
class SkepticContext:
    claim: Claim
    policies: SkepticPolicies
    deps: SkepticDeps

    # resolved artifacts
    evidence: list[EvidenceArtifact] = field(default_factory=list)
    related_evidence: list[EvidenceArtifact] = field(default_factory=list)
    anomalies: list[AnomalyArtifact] = field(default_factory=list)
    causal: list[CausalAnalysisArtifact] = field(default_factory=list)
    diagnostics: list[DiagnosticArtifact] = field(default_factory=list)
    forecasts: list[ForecastArtifact] = field(default_factory=list)
    strategies: list[StrategyArtifact] = field(default_factory=list)

    risk_score: float = 0.0
    risk_class: str = "R0"
    risk_components: dict[str, float] = field(default_factory=dict)
    challenge_plan: list[str] = field(default_factory=list)
    alternative_context: dict[str, Any] = field(default_factory=dict)
    risk_context: dict[str, Any] = field(default_factory=dict)

    def all_evidence(self) -> list[EvidenceArtifact]:
        seen: dict[str, EvidenceArtifact] = {}
        for ev in [*self.evidence, *self.related_evidence]:
            seen.setdefault(ev.evidence_id, ev)
        return list(seen.values())

    def synthetic_inputs(self) -> bool:
        pools: list[list[Any]] = [self.evidence, self.anomalies, self.causal, self.forecasts, self.strategies]
        return any(getattr(item, "synthetic", False) for pool in pools for item in pool)


@dataclass
class ValidatorOutcome:
    validator: str
    status: str  # OK | WEAK | REJECTED | UNAVAILABLE | NOT_APPLICABLE | INSUFFICIENT
    challenges: list[Challenge] = field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = field(default_factory=list)
    methodological_issues: list[str] = field(default_factory=list)
    followups: list[FollowUpTask] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    score_signals: dict[str, float] = field(default_factory=dict)

    @property
    def has_blocking(self) -> bool:
        return self.status == "REJECTED" or any(c.severity == "blocking" for c in self.challenges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator": self.validator,
            "status": self.status,
            "challenges": [c.model_dump() for c in self.challenges],
            "evidence_gaps": [g.model_dump() for g in self.evidence_gaps],
            "methodological_issues": list(self.methodological_issues),
            "followups": [f.model_dump() for f in self.followups],
            "detail": self.detail,
            "score_signals": self.score_signals,
        }

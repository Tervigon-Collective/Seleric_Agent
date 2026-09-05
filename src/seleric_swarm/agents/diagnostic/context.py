"""Shared runtime context + deps for the Diagnostic workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.agents.diagnostic.contracts import (
    DiagnosticHypothesis,
    DiagnosticRequest,
)
from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies
from seleric_swarm.agents.diagnostic.reasoning import NullReasoningModel, ReasoningModel
from seleric_swarm.agents.diagnostic.registries import (
    AnomalyRepository,
    ArtifactRepository,
    CausalEstimationService,
    CausalGraphRegistry,
    DeterministicStatsValidator,
    EvidenceRepository,
    IncidentRegistry,
    InMemoryAnomalyRepository,
    InMemoryArtifactRepository,
    InMemoryCausalGraphRegistry,
    InMemoryEvidenceRepository,
    InMemoryIncidentRegistry,
    StatisticalValidatorService,
    TemplateCausalEstimationService,
)
from seleric_swarm.services.ontology import OntologyPort


@dataclass(frozen=True)
class DiagnosticDeps:
    evidence_repo: EvidenceRepository = field(default_factory=InMemoryEvidenceRepository)
    artifact_repo: ArtifactRepository = field(default_factory=InMemoryArtifactRepository)
    anomaly_repo: AnomalyRepository = field(default_factory=InMemoryAnomalyRepository)
    causal_graphs: CausalGraphRegistry = field(default_factory=InMemoryCausalGraphRegistry)
    incident_registry: IncidentRegistry = field(default_factory=InMemoryIncidentRegistry)
    stats: StatisticalValidatorService = field(default_factory=DeterministicStatsValidator)
    causal_service: CausalEstimationService = field(default_factory=TemplateCausalEstimationService)
    reasoning: ReasoningModel = field(default_factory=NullReasoningModel)
    ontology: OntologyPort | None = None


@dataclass
class ScopedAnomaly:
    metric_id: str
    deviation_pct: float | None
    direction: str
    dimensions: dict[str, Any]
    start_time: str | None
    evidence_refs: list[str]
    raw: dict[str, Any]


@dataclass
class DiagnosticContext:
    request: DiagnosticRequest
    policies: DiagnosticPolicies
    deps: DiagnosticDeps

    evidence: list[dict[str, Any]] = field(default_factory=list)
    anomalies: list[ScopedAnomaly] = field(default_factory=list)
    outcome_metric: str = ""
    degradation_started_at: str | None = None
    event_times: dict[str, str] = field(default_factory=dict)  # event fact -> ISO time
    hypotheses: list[DiagnosticHypothesis] = field(default_factory=list)
    scratch: dict[str, Any] = field(default_factory=dict)
    # True when intake found no anomaly evidence at all and had to fall back to a
    # hardcoded default outcome metric — there is nothing confirmed to diagnose.
    no_confirmed_anomaly: bool = False

    def evidence_for_metric(self, metric_id: str) -> list[dict[str, Any]]:
        return [
            e for e in self.evidence
            if (e.get("metric_id") or e.get("metric_or_fact")) == metric_id
        ]

    def synthetic_inputs(self) -> bool:
        return any(e.get("synthetic") for e in self.evidence) or any(
            a.raw.get("synthetic") for a in self.anomalies
        )

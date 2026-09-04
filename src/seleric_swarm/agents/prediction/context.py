"""Shared runtime context + deps for the Prediction workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.agents.prediction.contracts import ForecastRun, PredictionRequest
from seleric_swarm.agents.prediction.policies import PredictionPolicies
from seleric_swarm.agents.prediction.reasoning import NullReasoningModel, ReasoningModel
from seleric_swarm.agents.prediction.registries import (
    ArtifactRepository,
    DriftMonitor,
    EvidenceRepository,
    FeatureStore,
    ForecastModelService,
    InMemoryArtifactRepository,
    InMemoryEvidenceRepository,
    InMemoryFeatureStore,
    InMemoryModelRegistry,
    ModelRegistry,
    NullDriftMonitor,
    StatisticalBaselineForecaster,
    TemplateForecastModelService,
)


@dataclass(frozen=True)
class PredictionDeps:
    evidence_repo: EvidenceRepository = field(default_factory=InMemoryEvidenceRepository)
    artifact_repo: ArtifactRepository = field(default_factory=InMemoryArtifactRepository)
    model_registry: ModelRegistry = field(default_factory=InMemoryModelRegistry)
    feature_store: FeatureStore = field(default_factory=InMemoryFeatureStore)
    model_service: ForecastModelService = field(default_factory=TemplateForecastModelService)
    baseline: StatisticalBaselineForecaster = field(default_factory=StatisticalBaselineForecaster)
    drift_monitor: DriftMonitor = field(default_factory=NullDriftMonitor)
    reasoning: ReasoningModel = field(default_factory=NullReasoningModel)


@dataclass
class PredictionContext:
    request: PredictionRequest
    policies: PredictionPolicies
    deps: PredictionDeps

    target_metric: str = ""
    horizon: str = ""
    history: list[float] = field(default_factory=list)
    current_value: float | None = None
    trend_pct: float | None = None
    drift_status: str | None = None
    cause_persistence: str = "high"        # "high" | "low"  -> scenario spread
    evidence: list[dict[str, Any]] = field(default_factory=list)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    causal_supported: bool = False

    run: ForecastRun | None = None
    scratch: dict[str, Any] = field(default_factory=dict)

    def synthetic_inputs(self) -> bool:
        return any(e.get("synthetic") for e in self.evidence)

    def mission_key(self) -> str:
        return f"{self.request.mission_id}:{self.target_metric}:{self.horizon}"

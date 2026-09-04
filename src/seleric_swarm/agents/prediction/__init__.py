"""Seleric Prediction Agent - forecast orchestration, not a forecasting LLM.

Public surface:

    from seleric_swarm.agents.prediction import (
        PredictionAgent, PredictionRequest, PredictionResult, PredictionDeps,
    )

    result = await PredictionAgent(...).predict(
        PredictionRequest(mission_id=..., target_metric=..., horizon="7d",
                          evidence_refs=[...], causal_refs=[...])
    )

Emits a ``ForecastArtifact`` + forecast ``Claim[]`` - the contracts the Skeptic's
model + forecast validators consume. Numbers come only from a registered model or
an approved statistical baseline; never an LLM. See ``docs/prediction/``.
"""

from __future__ import annotations

from seleric_swarm.agents.prediction.a2a import PredictionA2AAdapter
from seleric_swarm.agents.prediction.agent import PredictionAgent, prediction_deps_from_blackboard
from seleric_swarm.agents.prediction.context import PredictionContext, PredictionDeps
from seleric_swarm.agents.prediction.contracts import (
    Claim,
    ForecastArtifact,
    ForecastRun,
    PredictionRequest,
    PredictionResult,
    ScenarioProjection,
)
from seleric_swarm.agents.prediction.graph import build_prediction_graph
from seleric_swarm.agents.prediction.policies import PredictionPolicies

__all__ = [
    "Claim",
    "ForecastArtifact",
    "ForecastRun",
    "PredictionA2AAdapter",
    "PredictionAgent",
    "PredictionContext",
    "PredictionDeps",
    "PredictionPolicies",
    "PredictionRequest",
    "PredictionResult",
    "ScenarioProjection",
    "build_prediction_graph",
    "prediction_deps_from_blackboard",
]

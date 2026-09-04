"""Bridge: run the full Prediction subsystem inside the two-axis swarm loop.

``SwarmPredictionSpecialist`` matches the swarm specialist interface and writes a
``Prediction`` Blackboard artifact from the ``ForecastArtifact`` the subsystem
produces, so the synthesizer and existing tests are unchanged.

Enable per run: ``run_swarm_mission(runtime, query=..., full_prediction=True)``.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.prediction.agent import PredictionAgent, prediction_deps_from_blackboard
from seleric_swarm.agents.prediction.context import PredictionDeps
from seleric_swarm.agents.prediction.contracts import PredictionRequest, PredictionResult
from seleric_swarm.agents.prediction.policies import PredictionPolicies
from seleric_swarm.agents.prediction.registries import (
    FeatureSetRef,
    InMemoryFeatureStore,
    InMemoryModelRegistry,
    ModelRecord,
    TemplateForecastModelService,
)
from seleric_swarm.swarm.artifacts import Prediction
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission


class SwarmPredictionSpecialist:
    agent_class = "specialist"
    agent_id = "prediction_agent"
    capability = "forecasting"
    produces = "prediction"

    def __init__(
        self,
        providers: Any = None,
        *,
        scenario: dict[str, Any] | None = None,
        deps: PredictionDeps | None = None,
        policies: PredictionPolicies | None = None,
    ) -> None:
        self.providers = providers
        self._scenario = scenario or {}
        self._deps = deps
        self._policies = policies or PredictionPolicies.load()

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return mission.wants("predictive")

    def _fixture_deps(self) -> PredictionDeps:
        """Fixture / replay mode: the scenario's declared forecast model is
        authoritative, so register it as 'approved' and route through the
        template model service."""

        truth = self._scenario.get("forecast_truth", {}) or {}
        model = truth.get("model", {}) or {}
        reg = InMemoryModelRegistry()
        fs = InMemoryFeatureStore()
        mid = model.get("id") or "forecast.fixture.v1"
        if truth.get("target"):
            reg.add(
                ModelRecord(
                    model_id=mid,
                    version=str(model.get("version", "1")),
                    status="approved",
                    target=str(truth["target"]),
                    backtest_available=bool(model.get("backtest_metric") or model.get("mape") is not None),
                )
            )
            fs.add(mid, FeatureSetRef(feature_set_id=model.get("feature_set") or f"{mid}.features", version="1"))
        return PredictionDeps(
            model_registry=reg,
            feature_store=fs,
            model_service=TemplateForecastModelService(truth),
        )

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        base = self._deps or self._fixture_deps()
        deps = prediction_deps_from_blackboard(blackboard, base=base)
        agent = PredictionAgent(deps=deps, policies=self._policies)

        request = PredictionRequest(
            mission_id=blackboard.mission_id,
            question=mission.query,
            target_metric=str(mission.context.get("primary_metric") or ""),
            horizon="7d",
            evidence_refs=list(blackboard.evidence_ledger),
            causal_refs=blackboard.refs_by_type("causal"),
            lead_domain=blackboard.mission_lead,
            time_range=dict(mission.time_range),
        )
        result: PredictionResult = await agent.predict(request)

        if not result.has_forecast():
            blackboard.record_event("prediction_insufficient", target=result.target_metric,
                                    reasons=result.audit.get("fallback_reasons"))
            return []

        art = _to_artifact(blackboard, result)
        if result.synthetic:
            art.mark_synthetic()
        blackboard.record_event(
            "prediction_done",
            target=result.target_metric,
            source=result.source,
            confidence=result.confidence,
            applicability=result.applicability,
        )
        return [blackboard.post(art)]


def _to_artifact(blackboard: Blackboard, result: PredictionResult) -> Prediction:
    fa = result.forecast_artifact
    assert fa is not None
    secondary: dict[str, Any] = {}
    truth_secondary = result.audit.get("secondary")
    if isinstance(truth_secondary, dict):
        secondary = truth_secondary
    return Prediction.new(
        mission_id=blackboard.mission_id,
        created_by="prediction_agent",
        target=fa.target_metric,
        horizon=fa.horizon,
        model={
            "id": fa.model_id,
            "version": fa.model_version,
            "feature_set": fa.feature_set_id,
            "drift_status": fa.drift_status,
            "backtest": fa.backtest_metrics,
        },
        prediction=fa.prediction,
        interval=fa.interval,
        drift_status=fa.drift_status,
        secondary=secondary,
        evidence_refs=blackboard.refs_by_type("causal"),
        quality_flags=[f"confidence:{result.confidence}", f"source:{result.source}",
                       f"applicability:{result.applicability}"],
    )

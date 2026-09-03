"""Prediction - "What happens next if this continues?" (architecture sec. 10-11).

A forecast *orchestration* agent, not a forecasting LLM. Routes to a registered
model via ``Forecaster``; returns INSUFFICIENT_EVIDENCE rather than improvising.
"""

from __future__ import annotations

from seleric_swarm.swarm.artifacts import Prediction
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.specialists.base import SpecialistAgent


class PredictionAgent(SpecialistAgent):
    agent_id = "prediction_agent"
    capability = "forecasting"
    produces = "prediction"

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return mission.wants("predictive")

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        target = str(mission.context.get("primary_metric") or "metric.cac")
        features = {
            "anomalies": [a["metric_id"] for a in blackboard.by_type("anomaly")],
            "retained_hypotheses": [
                h["artifact_id"] for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"
            ],
        }
        result = await self.providers.forecaster.forecast(target=target, horizon="7d", features=features)
        if result is None:
            blackboard.record_event("prediction_insufficient", target=target)
            return []
        art = Prediction.new(
            mission_id=blackboard.mission_id,
            created_by=self.agent_id,
            target=result.target,
            horizon=result.horizon,
            model=result.model,
            prediction=result.prediction,
            interval=result.interval,
            drift_status=result.drift_status,
            secondary=result.secondary,
            data_origin=result.data_origin,  # type: ignore[arg-type]
            evidence_refs=blackboard.refs_by_type("causal"),
        )
        if result.synthetic:
            art.mark_synthetic()
        ok, problems = self.validate(art.model_dump())
        if not ok:
            blackboard.record_event("prediction_rejected", problems=problems)
            return []
        return [blackboard.post(art)]

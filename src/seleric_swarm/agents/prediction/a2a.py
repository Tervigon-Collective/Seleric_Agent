"""Thin A2A adapter for the Prediction Agent."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.prediction.agent import PredictionAgent
from seleric_swarm.agents.prediction.contracts import PredictionRequest, PredictionResult

SUPPORTED_INTENTS = {"predict", "forecast", "risk_prediction", "model_request", "task_request"}


class PredictionA2AAdapter:
    def __init__(self, agent: PredictionAgent) -> None:
        self._agent = agent

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        intent = str(payload.get("intent") or "predict")
        if intent not in SUPPORTED_INTENTS:
            return {"ok": False, "error": f"prediction does not handle intent '{intent}'"}
        request = PredictionRequest(
            mission_id=str(payload.get("mission_id") or ""),
            question=str(payload.get("question") or payload.get("objective") or ""),
            target_metric=str(payload.get("target_metric") or ""),
            horizon=str(payload.get("horizon") or ""),
            evidence_refs=list(payload.get("evidence_refs") or []),
            anomaly_refs=list(payload.get("anomaly_refs") or []),
            diagnostic_refs=list(payload.get("diagnostic_refs") or []),
            causal_refs=list(payload.get("causal_refs") or []),
            lead_domain=payload.get("lead_domain"),
            time_range=dict(payload.get("time_range") or payload.get("scope") or {}),
            context=dict(payload.get("context") or {}),
            history=payload.get("history"),
        )
        result = await self._agent.predict(request)
        return _as_artifact_response(payload, result)

    async def as_handler(self, message: Any) -> dict[str, Any]:
        payload = {
            "mission_id": getattr(message, "mission_id", ""),
            "task_id": getattr(message, "task_id", None),
            "intent": getattr(getattr(message, "intent", None), "value", "predict"),
            "objective": getattr(message, "objective", ""),
            "evidence_refs": list(getattr(message, "evidence_refs", []) or []),
            "scope": dict(getattr(message, "scope", {}) or {}),
            **(getattr(message, "payload", {}) or {}),
        }
        return await self.handle(payload)


def _as_artifact_response(payload: dict[str, Any], result: PredictionResult) -> dict[str, Any]:
    return {
        "ok": True,
        "protocol": "seleric.swarm.v1",
        "mission_id": result.mission_id,
        "task_id": payload.get("task_id"),
        "intent": "artifact_response",
        "produced": "forecast_artifact" if result.has_forecast() else "insufficient_predictive_evidence",
        "forecast_artifact": result.forecast_artifact.model_dump() if result.forecast_artifact else None,
        "scenarios": [s.model_dump() for s in result.scenarios],
        "claims": [c.model_dump() for c in result.claims],
        "confidence": result.confidence,
        "source": result.source,
    }

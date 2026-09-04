"""Thin A2A adapter for the Diagnostic Agent."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.diagnostic.agent import DiagnosticAgent
from seleric_swarm.agents.diagnostic.contracts import DiagnosticRequest, DiagnosticResult

SUPPORTED_INTENTS = {"diagnose", "hypothesis_generation", "causal_diagnosis", "model_request", "task_request"}


class DiagnosticA2AAdapter:
    def __init__(self, agent: DiagnosticAgent) -> None:
        self._agent = agent

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        intent = str(payload.get("intent") or "diagnose")
        if intent not in SUPPORTED_INTENTS:
            return {"ok": False, "error": f"diagnostic does not handle intent '{intent}'"}
        request = DiagnosticRequest(
            mission_id=str(payload.get("mission_id") or ""),
            question=str(payload.get("question") or payload.get("objective") or ""),
            primary_metric=str(payload.get("primary_metric") or ""),
            outcome_metric=str(payload.get("outcome_metric") or ""),
            anomaly_refs=list(payload.get("anomaly_refs") or []),
            evidence_refs=list(payload.get("evidence_refs") or []),
            lead_domain=payload.get("lead_domain"),
            time_range=dict(payload.get("time_range") or payload.get("scope") or {}),
            degradation_started_at=payload.get("degradation_started_at"),
            context=dict(payload.get("context") or {}),
        )
        result = await self._agent.diagnose(request)
        return _as_artifact_response(payload, result)

    async def as_handler(self, message: Any) -> dict[str, Any]:
        payload = {
            "mission_id": getattr(message, "mission_id", ""),
            "task_id": getattr(message, "task_id", None),
            "intent": getattr(getattr(message, "intent", None), "value", "diagnose"),
            "objective": getattr(message, "objective", ""),
            "evidence_refs": list(getattr(message, "evidence_refs", []) or []),
            "scope": dict(getattr(message, "scope", {}) or {}),
            **(getattr(message, "payload", {}) or {}),
        }
        return await self.handle(payload)


def _as_artifact_response(payload: dict[str, Any], result: DiagnosticResult) -> dict[str, Any]:
    return {
        "ok": True,
        "protocol": "seleric.swarm.v1",
        "mission_id": result.mission_id,
        "task_id": payload.get("task_id"),
        "intent": "artifact_response",
        "produced": "diagnostic_artifact",
        "diagnostic_artifact": result.diagnostic_artifact.model_dump() if result.diagnostic_artifact else None,
        "causal_artifact": result.causal_artifact.model_dump() if result.causal_artifact else None,
        "claims": [c.model_dump() for c in result.claims],
        "finding": result.finding.model_dump() if result.finding else None,
    }

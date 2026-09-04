"""Thin A2A adapter for the Skeptic (spec sec. 42).

Keeps protocol concerns out of the agent. Accepts a ``seleric.swarm.v1``-style
inbound payload (or the repo's :class:`SwarmMessage`) and returns the
:class:`SkepticVerdict` wrapped as an ``artifact_response``. The domain logic is
protocol-independent; only this file knows about envelopes.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.skeptic.agent import SkepticAgent
from seleric_swarm.agents.skeptic.contracts import (
    Claim,
    SkepticValidationRequest,
    SkepticVerdict,
)

SUPPORTED_INTENTS = {
    "claim_validation",
    "artifact_validation",
    "hypothesis_challenge",
    "completion_candidate",
    "challenge",
}


class SkepticA2AAdapter:
    def __init__(self, agent: SkepticAgent) -> None:
        self._agent = agent

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        intent = str(payload.get("intent") or "claim_validation")
        if intent not in SUPPORTED_INTENTS:
            return {"ok": False, "error": f"skeptic does not handle intent '{intent}'"}

        claim_payload = payload.get("claim") or _claim_from_refs(payload)
        request = SkepticValidationRequest(
            mission_id=str(payload.get("mission_id") or claim_payload.get("mission_id") or ""),
            claim=Claim(**claim_payload) if not isinstance(claim_payload, Claim) else claim_payload,
            evidence_refs=list(payload.get("evidence_refs") or []),
            risk_context=dict(payload.get("risk_context") or {}),
            available_artifact_refs=list(payload.get("available_artifact_refs") or payload.get("artifact_refs") or []),
        )
        verdict = await self._agent.validate_claim(request)
        return _as_artifact_response(payload, verdict)

    # in-process transport handler signature: (SwarmMessage) -> dict
    async def as_handler(self, message: Any) -> dict[str, Any]:
        payload = {
            "mission_id": getattr(message, "mission_id", ""),
            "task_id": getattr(message, "task_id", None),
            "intent": getattr(getattr(message, "intent", None), "value", "claim_validation"),
            "evidence_refs": list(getattr(message, "evidence_refs", []) or []),
            "artifact_refs": list(getattr(message, "artifact_refs", []) or []),
            **(getattr(message, "payload", {}) or {}),
        }
        return await self.handle(payload)


def _claim_from_refs(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "mission_id": payload.get("mission_id", ""),
        "claim_type": payload.get("claim_type", "qualitative"),
        "statement": payload.get("statement", payload.get("objective", "")),
        "origin_agent": payload.get("from_agent", "unknown_agent"),
        "support_refs": list(payload.get("evidence_refs") or []),
        "causal_refs": list(payload.get("causal_refs") or []),
        "forecast_refs": list(payload.get("forecast_refs") or []),
        "strategy_refs": list(payload.get("strategy_refs") or []),
        "anomaly_refs": list(payload.get("anomaly_refs") or []),
    }


def _as_artifact_response(payload: dict[str, Any], verdict: SkepticVerdict) -> dict[str, Any]:
    return {
        "ok": True,
        "protocol": "seleric.swarm.v1",
        "mission_id": verdict.mission_id,
        "task_id": payload.get("task_id"),
        "intent": "artifact_response",
        "produced": "skeptic_verdict",
        "artifact": verdict.model_dump(),
        "verdict": verdict.verdict,
        "required_followups": [t.model_dump() for t in verdict.required_followups],
    }

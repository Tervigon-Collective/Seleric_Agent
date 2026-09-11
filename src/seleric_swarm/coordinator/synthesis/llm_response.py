"""LLM synthesis for swarm_v2 missions — answers the specific question asked,
instead of `response_builder.py`'s fixed "Key anomalies" dump. Same pattern as
`orchestration/synthesize.py`'s lookup_v1 synthesizer: plain-text LLM output,
audited against the numbers actually present in the mission's own artifacts,
falling back to the deterministic template on any LLM failure or numeric leak.
"""

from __future__ import annotations

import json
from typing import Any

from seleric_swarm.coordinator.policies import CoordinatorPolicies
from seleric_swarm.coordinator.synthesis.claim_selector import select_allowed_claims
from seleric_swarm.coordinator.synthesis.response_builder import build_claim_aware_response
from seleric_swarm.llm.errors import LLMError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.numeric_audit import extract_numbers, unaudited_numbers
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission

AGENT_VERSION = "0.1.0"


def _numeric_pool(*payloads: Any) -> list[Any]:
    """Every number appearing anywhere in what was actually shown to the LLM —
    the audit must judge against the full payload, not a hand-picked subset of
    its fields (an anomaly's `observed`/`expected_range`, not just its
    `deviation_pct`, are equally legitimate for the LLM to cite)."""
    extra: list[Any] = []
    for payload in payloads:
        if not payload:
            continue
        extra.extend(extract_numbers(json.dumps(payload, default=str)))
    return extra


def _is_rounding_of_allowed(token: str, allowed: list[Any]) -> bool:
    """A business-readable answer legitimately rounds a raw figure (deviation_pct
    etc. carry full float precision, e.g. 85.294117647 -> "85.3%"). The audit
    must not treat that as fabrication — only a number with no real basis at
    any rounding precision counts as a leak.
    """
    try:
        t = float(token)
    except ValueError:
        return False
    for item in allowed:
        try:
            a = float(item)
        except (TypeError, ValueError):
            continue
        if any(round(a, d) == t for d in (0, 1, 2)):
            return True
    return False


async def synthesize_swarm_response(
    *,
    runtime: SwarmRuntime,
    blackboard: Blackboard,
    mission: SwarmMission,
    managed_claims: list[dict[str, Any]] | None = None,
    completion_status: str | None = None,
    policies: CoordinatorPolicies | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    extra_limitations: list[str] | None = None,
    mission_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
) -> str:
    def fallback() -> str:
        return build_claim_aware_response(
            blackboard,
            mission,
            managed_claims=managed_claims,
            completion_status=completion_status,
            policies=policies,
            conflicts=conflicts,
            extra_limitations=extra_limitations,
        )

    try:
        spec = runtime.prompts.load("synthesizer.swarm_response")
    except Exception:
        return fallback()

    claims = select_allowed_claims(list(managed_claims or []))
    anomalies = _anomalies_for_answer(blackboard, mission)
    predictions = blackboard.by_type("prediction")
    prediction = predictions[0] if predictions else None
    strategies = blackboard.by_type("strategy")
    recommendation = strategies[0] if strategies else None
    skeptic_arts = blackboard.by_type("skeptic")
    latest_skeptic = skeptic_arts[-1] if skeptic_arts else None

    user = spec.render_user(
        {
            "query": mission.query,
            "claims_json": json.dumps(claims, default=str),
            "anomalies_json": json.dumps(anomalies, default=str),
            "prediction_json": json.dumps(prediction, default=str) if prediction else "none",
            "recommendation_json": json.dumps(recommendation, default=str) if recommendation else "none",
            "skeptic_verdict": str((latest_skeptic or {}).get("verdict") or "none"),
            "skeptic_followups": json.dumps((latest_skeptic or {}).get("required_followups") or []),
        }
    )
    request = LLMRequest(
        messages=[
            ChatMessage(role="system", content=spec.system),
            ChatMessage(role="user", content=user),
        ],
        model=spec.model,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        timeout_s=runtime.settings.llm_timeout_s,
        metadata=LLMRequestMetadata(
            request_id=request_id or mission.mission_id,
            session_id=session_id or mission.mission_id,
            mission_id=mission_id or mission.mission_id,
            agent_id="coordinator_agent",
            agent_version=runtime.agents.version("coordinator_agent", AGENT_VERSION),
            prompt_id=spec.id,
            prompt_version=spec.version,
            workflow_name=runtime.settings.workflow_name,
            workflow_version=runtime.settings.workflow_version,
            model=spec.model,
        ),
        tags=["synthesizer", "swarm_response", spec.id],
    )
    try:
        raw = await runtime.llm.complete(request)
    except LLMError:
        return fallback()

    prose = raw.text.strip()
    if not prose:
        return fallback()

    extra_allowed = _numeric_pool(claims, anomalies, prediction, recommendation)
    leaked = unaudited_numbers(prose, [], extra_allowed)
    if any(not _is_rounding_of_allowed(token, extra_allowed) for token in leaked):
        return fallback()
    return prose


def _asked_metric_id(mission: SwarmMission) -> str:
    ctx = mission.context or {}
    return str(ctx.get("primary_metric") or ctx.get("resolved_metric") or "")


def _anomalies_for_answer(blackboard: Blackboard, mission: SwarmMission) -> list[dict[str, Any]]:
    """Top movers, with the asked metric pinned even when quieter than peers.

    Without this, a ROAS question can be answered from louder checkout/revenue
    anomalies while the payload never mentions ``metric.gross_roas``.
    """
    all_anoms = list(blackboard.by_type("anomaly"))
    primary = _asked_metric_id(mission)
    ranked = sorted(all_anoms, key=lambda x: abs(x.get("deviation_pct") or 0), reverse=True)
    pinned = [a for a in all_anoms if primary and a.get("metric_id") == primary]
    rest = [a for a in ranked if not primary or a.get("metric_id") != primary][:8]
    out: list[dict[str, Any]] = list(pinned) + rest
    if primary and not pinned:
        evidence = [
            e
            for e in blackboard.by_type("evidence")
            if e.get("metric_id") == primary or e.get("metric_or_fact") == primary
        ]
        if evidence:
            row = evidence[0]
            out.insert(
                0,
                {
                    "metric_id": primary,
                    "observed": row.get("value"),
                    "deviation_pct": row.get("change_pct"),
                    "direction": "up" if (row.get("change_pct") or 0) > 0 else "down",
                    "from_evidence": True,
                },
            )
    return out

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
from seleric_swarm.coordinator.synthesis.response_builder import (
    build_claim_aware_response,
    clean_anomalies,
    gapped_metric_ids,
)
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
    etc. carry full float precision, e.g. -94.285714 -> "94.3%"). The audit
    must not treat that as fabrication — comparing absolute values accounts for
    prose describing negative movements as positive drop magnitudes.
    """
    try:
        t = float(token)
    except ValueError:
        return False
    abs_t = abs(t)
    for item in allowed:
        try:
            a = float(item)
        except (TypeError, ValueError):
            continue
        abs_a = abs(a)
        if any(round(a, d) == t or round(abs_a, d) == abs_t for d in (0, 1, 2)):
            return True
    return False


def _clean_anomaly_for_prompt(a: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    mid = str(a.get("metric_id") or "")
    if mid:
        out["metric_name"] = mid.removeprefix("metric.").replace("_", " ").title()
    if isinstance(a.get("deviation_pct"), (int, float)):
        out["deviation_pct"] = round(float(a["deviation_pct"]), 2)
    if isinstance(a.get("observed"), (int, float)):
        out["observed"] = round(float(a["observed"]), 2)
    exp = a.get("expected_range")
    if isinstance(exp, (list, tuple)):
        out["expected_range"] = [round(float(x), 2) if isinstance(x, (int, float)) else x for x in exp]
    return out


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
            metrics=runtime.metrics,
        )

    try:
        spec = runtime.prompts.load("synthesizer.swarm_response")
    except Exception:
        return fallback()

    claims = select_allowed_claims(list(managed_claims or []))
    retained_hypotheses = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
    anomalies = _anomalies_for_answer(
        blackboard, mission, metrics=runtime.metrics, limitations=extra_limitations
    )
    anomalies_prompt = [_clean_anomaly_for_prompt(a) for a in anomalies]
    predictions = blackboard.by_type("prediction")
    prediction = predictions[0] if predictions else None
    strategies = blackboard.by_type("strategy")
    recommendation = strategies[0] if strategies else None
    skeptic_arts = blackboard.by_type("skeptic")
    latest_skeptic = skeptic_arts[-1] if skeptic_arts else None

    user = spec.render_user(
        {
            "query": mission.query,
            "completion_status": completion_status or "unknown",
            "claims_json": json.dumps(claims, default=str),
            "hypotheses_json": json.dumps(retained_hypotheses, default=str),
            "anomalies_json": json.dumps(anomalies_prompt, default=str),
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

    extra_allowed = _numeric_pool(
        mission.query,
        mission.time_range,
        claims,
        retained_hypotheses,
        blackboard.by_type("hypothesis"),
        blackboard.by_type("causal"),
        blackboard.by_type("evidence"),
        anomalies,
        anomalies_prompt,
        prediction,
        recommendation,
    )
    leaked = unaudited_numbers(prose, [], extra_allowed, query_text=mission.query)
    if any(not _is_rounding_of_allowed(token, extra_allowed) for token in leaked):
        return fallback()
    return prose


def _asked_metric_id(mission: SwarmMission) -> str:
    ctx = mission.context or {}
    return str(ctx.get("primary_metric") or ctx.get("resolved_metric") or "")


def _anomalies_for_answer(
    blackboard: Blackboard,
    mission: SwarmMission,
    *,
    metrics: Any = None,
    limitations: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Top movers, with the asked metric pinned even when quieter than peers.

    Without this, a ROAS question can be answered from louder checkout/revenue
    anomalies while the payload never mentions ``metric.gross_roas``.

    Deduped by canonical metric id and stripped of anything with a reported
    data gap for this window (see ``clean_anomalies``) — the LLM must never
    be handed a number that has no data behind it or the same finding twice
    under two id spellings.
    """
    all_anoms = clean_anomalies(
        blackboard.by_type("anomaly"), limitations=limitations, metrics=metrics
    )
    primary = _asked_metric_id(mission)
    primary_canon = metrics.canonical_id(primary) if (metrics is not None and primary) else primary
    ranked = sorted(all_anoms, key=lambda x: abs(x.get("deviation_pct") or 0), reverse=True)

    def _canon(mid: Any) -> Any:
        return metrics.canonical_id(mid) if (metrics is not None and mid) else mid

    pinned = [a for a in all_anoms if primary and _canon(a.get("metric_id")) == primary_canon]
    rest = [a for a in ranked if not primary or _canon(a.get("metric_id")) != primary_canon][:8]
    out: list[dict[str, Any]] = list(pinned) + rest
    if primary and not pinned and primary_canon not in gapped_metric_ids(limitations, metrics):
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

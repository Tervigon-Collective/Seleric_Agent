"""Normalize an inbound claim.

Accepts either a fully-formed :class:`Claim`, the repo's lean
``seleric_swarm.domain.models.Claim`` dict, or a raw dict from an A2A payload,
and returns a canonical :class:`Claim`. Reference lists are de-duplicated and the
union of all typed ref lists is mirrored into ``support_refs`` so downstream
loaders have one place to look.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from seleric_swarm.agents.skeptic.contracts import Claim, ClaimType

_LEGACY_TYPE_MAP: dict[str, ClaimType] = {
    "numeric": "numeric",
    "comparison": "comparison",
    "causal": "causal",
    "forecast": "forecast",
    "recommendation": "recommendation",
    "action": "action",
    "qualitative": "qualitative",
    "anomaly": "anomaly",
    "correlation": "correlation",
}


def parse_claim(payload: Claim | dict[str, Any], *, mission_id: str | None = None) -> Claim:
    if isinstance(payload, Claim):
        claim = payload.model_copy(deep=True)
    else:
        data = dict(payload)
        statement = data.get("statement") or data.get("text") or ""
        ctype = _LEGACY_TYPE_MAP.get(str(data.get("claim_type") or "qualitative"), "qualitative")
        claim = Claim(
            claim_id=data.get("claim_id") or f"CL-{uuid4().hex[:12]}",
            mission_id=data.get("mission_id") or mission_id or "",
            claim_type=ctype,
            statement=statement,
            origin_agent=data.get("origin_agent") or data.get("created_by") or "unknown_agent",
            support_refs=list(data.get("support_refs") or []),
            contradiction_refs=list(data.get("contradiction_refs") or []),
            metric_refs=list(data.get("metric_refs") or []),
            anomaly_refs=list(data.get("anomaly_refs") or []),
            causal_refs=_as_list(data.get("causal_refs") or data.get("causal_ref")),
            diagnostic_refs=list(data.get("diagnostic_refs") or []),
            model_refs=_as_list(data.get("model_refs") or data.get("model_ref")),
            forecast_refs=list(data.get("forecast_refs") or []),
            strategy_refs=list(data.get("strategy_refs") or []),
            metadata=dict(data.get("metadata") or {}),
        )

    if mission_id and not claim.mission_id:
        claim.mission_id = mission_id

    typed = [
        *claim.metric_refs, *claim.anomaly_refs, *claim.causal_refs, *claim.diagnostic_refs,
        *claim.model_refs, *claim.forecast_refs, *claim.strategy_refs,
    ]
    merged: list[str] = []
    for ref in [*claim.support_refs, *typed]:
        if ref and ref not in merged:
            merged.append(ref)
    claim.support_refs = merged

    for attr in ("metric_refs", "anomaly_refs", "causal_refs", "diagnostic_refs", "model_refs", "forecast_refs", "strategy_refs", "contradiction_refs"):
        setattr(claim, attr, _dedupe(getattr(claim, attr)))
    return claim


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v]


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out

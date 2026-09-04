"""Applicability / regime check.

Decides whether the current data is in-domain for the produced forecast:
history length, an explicit regime/applicability status on the request, and a
drift signal. A reject status here forces INSUFFICIENT (or a REJECT-worthy
artifact the Skeptic will block).
"""

from __future__ import annotations

from seleric_swarm.agents.prediction.context import PredictionContext
from seleric_swarm.agents.prediction.contracts import ApplicabilityStatus


def assess_applicability(ctx: PredictionContext) -> tuple[ApplicabilityStatus, list[str]]:
    notes: list[str] = []
    pol = ctx.policies

    declared = str(ctx.request.context.get("applicability_status") or "").lower()
    if declared in pol.applic_reject_statuses():
        notes.append(f"caller declared applicability '{declared}'")
        return _map(declared), notes

    # regime shift: a very large recent move relative to history spread
    if len(ctx.history) >= 4:
        spread = max(ctx.history) - min(ctx.history) or 1.0
        last_step = abs(ctx.history[-1] - ctx.history[-2])
        if last_step > 1.5 * (spread / len(ctx.history)) * 4:
            notes.append("recent move is large relative to historical variation (possible regime shift)")
            return "regime_shift", notes

    # history sufficiency (approx: 1 point ~ 1 day for these fixtures)
    approx_days = len(ctx.history)
    if approx_days and approx_days < pol.applic_min_history_days() // 3:
        notes.append(f"short history (~{approx_days} points)")
        return "near_domain", notes

    drift = (ctx.drift_status or "").lower()
    if drift in pol.drift_reject_statuses():
        notes.append(f"drift status '{ctx.drift_status}'")
        return "out_of_domain", notes
    if drift in {"amber", "yellow"}:
        notes.append(f"drift status '{ctx.drift_status}' (elevated)")
        return "near_domain", notes

    if not ctx.history:
        notes.append("no history available to assess regime")
        return "unknown", notes

    return "in_domain", notes


def _map(status: str) -> ApplicabilityStatus:
    return {
        "out_of_domain": "out_of_domain",
        "regime_shift": "regime_shift",
        "not_applicable": "out_of_domain",
    }.get(status, "unknown")  # type: ignore[return-value]

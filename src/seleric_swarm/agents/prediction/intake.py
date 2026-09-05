"""Intake: resolve target metric + horizon, extract current level / trend / drift,
and note whether a diagnosed cause is causally supported (drives scenario spread).
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.prediction.context import PredictionContext


async def resolve_intake(ctx: PredictionContext) -> None:
    req = ctx.request
    deps = ctx.deps

    ev_ids = list(dict.fromkeys(req.evidence_refs))
    rows = await deps.evidence_repo.get_many(ev_ids) if ev_ids else []
    if not rows:
        rows = await deps.evidence_repo.search_related(req.mission_id)
    ctx.evidence = list(rows)

    ctx.anomalies = []
    for ref in req.anomaly_refs:
        got = await deps.artifact_repo.get(ref)
        if got:
            ctx.anomalies.append(got)
    if not ctx.anomalies:
        ctx.anomalies = await deps.artifact_repo.by_type(req.mission_id, "anomaly")

    ctx.horizon = req.horizon or ctx.policies.default_horizon()
    ctx.target_metric = _resolve_target(req.target_metric, ctx.anomalies, ctx.evidence)

    # current level + trend from evidence for the target
    target_rows = [
        e for e in ctx.evidence
        if (e.get("metric_id") or e.get("metric_or_fact")) == ctx.target_metric
    ]
    if target_rows:
        row = target_rows[0]
        ctx.current_value = _num(row.get("value"))
        ctx.trend_pct = _num(row.get("change_pct"))

    # history: explicit list, else synthesised from current + change_pct
    if req.history:
        ctx.history = [float(x) for x in req.history]
    elif ctx.current_value is not None and ctx.trend_pct is not None:
        ctx.history = _synth_history(ctx.current_value, ctx.trend_pct)

    # drift status from a monitor or context
    ctx.drift_status = req.context.get("drift_status")

    # causal support -> cause persistence (wide vs narrow scenario band)
    causal_rows: list[dict[str, Any]] = []
    for ref in req.causal_refs:
        got = await deps.artifact_repo.get(ref)
        if got:
            causal_rows.append(got)
    if not causal_rows:
        causal_rows = await deps.artifact_repo.by_type(req.mission_id, "causal")
    ctx.causal_supported = any(c.get("passed") for c in causal_rows)
    ctx.cause_persistence = "high" if ctx.causal_supported else "low"


def _resolve_target(hint: str, anomalies: list[dict], evidence: list[dict]) -> str:
    if hint:
        return hint
    # No static metric-name priority list: pick the most-deviated real anomaly,
    # else the first real evidence metric. If nothing is available, report
    # unresolved (target_metric == "") rather than guessing a plausible id.
    if anomalies:
        return max(anomalies, key=lambda a: abs(a.get("deviation_pct") or 0)).get("metric_id", "")
    for e in evidence:
        m = e.get("metric_id") or e.get("metric_or_fact")
        if m and not str(m).startswith("event."):
            return str(m)
    return ""


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _synth_history(current: float, change_pct: float, points: int = 10) -> list[float]:
    """Reconstruct a plausible recent series ending at ``current`` given the total
    percent change over the window. Linear on the log-return, most-recent last."""
    start = current / (1 + change_pct / 100.0) if change_pct != -100 else current * 0.5
    step = (current - start) / (points - 1)
    return [round(start + step * i, 4) for i in range(points)]

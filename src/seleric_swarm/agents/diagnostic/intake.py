"""Intake: resolve the outcome metric, scope the anomalies, extract event times."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.diagnostic.context import DiagnosticContext, ScopedAnomaly
from seleric_swarm.services.metrics import MetricRegistry


async def resolve_intake(ctx: DiagnosticContext) -> None:
    req = ctx.request
    deps = ctx.deps

    # -- evidence ------------------------------------------------------
    ev_ids: list[str] = []
    for ref in [*req.evidence_refs]:
        if ref and ref not in ev_ids:
            ev_ids.append(ref)
    rows = await deps.evidence_repo.get_many(ev_ids) if ev_ids else []
    if not rows:
        rows = await deps.evidence_repo.search_related(req.mission_id)
    ctx.evidence = list(rows)

    # -- event times (event.* facts carry an ISO timestamp as their value) --
    for e in ctx.evidence:
        key = e.get("metric_id") or e.get("metric_or_fact") or ""
        if str(key).startswith("event.") and e.get("value"):
            ctx.event_times[str(key)] = str(e["value"])

    # -- anomalies -------------------------------------------------
    an_rows: list[dict[str, Any]] = []
    if req.anomaly_refs:
        an_rows = await deps.anomaly_repo.get_many(req.anomaly_refs)
    if not an_rows:
        an_rows = await deps.anomaly_repo.by_mission(req.mission_id)
    ctx.anomalies = [_scope(a) for a in an_rows]

    # -- outcome metric ------------------------------------------
    ctx.outcome_metric = _resolve_outcome(
        req.outcome_metric or req.primary_metric,
        ctx.anomalies,
        metrics=deps.metrics,
    )

    # No anomaly evidence at all, and no caller-supplied metric hint either —
    # there is nothing confirmed to diagnose. Do not force a root-cause story
    # against a hardcoded default metric (spec: NO_CONFIRMED_ANOMALY).
    hint = req.outcome_metric or req.primary_metric
    ctx.no_confirmed_anomaly = not ctx.anomalies and not hint

    # -- degradation start -------------------------------------
    ctx.degradation_started_at = (
        req.degradation_started_at
        or next((a.start_time for a in ctx.anomalies if a.metric_id == ctx.outcome_metric and a.start_time), None)
        or next((a.start_time for a in ctx.anomalies if a.start_time), None)
        or req.context.get("degradation_started_at")
    )


def _scope(a: dict[str, Any]) -> ScopedAnomaly:
    return ScopedAnomaly(
        metric_id=a.get("metric_id", ""),
        deviation_pct=a.get("deviation_pct"),
        direction=a.get("direction", "unknown"),
        dimensions=a.get("dimensions") or {},
        start_time=a.get("start_time") or (a.get("analysis_window") or {}).get("start"),
        evidence_refs=a.get("evidence_refs") or [],
        raw=a,
    )


def _resolve_outcome(
    hint: str,
    anomalies: list[ScopedAnomaly],
    *,
    metrics: MetricRegistry | None = None,
) -> str:
    """The asked metric stays the asked metric.

    Leadership transfer can still recommend a different domain; intake must not
    secretly rewrite technical → purchase_cvr (or any other frontier KPI).
    """
    metric_ids = {a.metric_id for a in anomalies}
    if hint:
        registry = metrics or MetricRegistry("config/metric_registry.yaml")
        if registry.get(hint) is not None or hint in metric_ids:
            return hint
    if anomalies:
        return max(anomalies, key=lambda a: abs(a.deviation_pct or 0)).metric_id
    return hint

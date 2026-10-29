"""The latest business-state snapshot, as the data the agent loop's ``business_snapshot`` tool returns.

No model call and no formatting: the loop's model phrases the answer. This module only decides whether a snapshot
is fresh enough to use and turns it into a compact dict (period, baseline and unit stated, not left to the model:
live 2026-10-07 a one-day value was described as a 7-day range and INR as "$").
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from seleric_swarm.toolsets import policy_config as P

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime
    from seleric_swarm.services.domain_health.models import DomainStateSnapshot


def snapshot_age_hours(snapshot: DomainStateSnapshot) -> float | None:
    """Hours since the snapshot was computed, or None if unparseable."""
    try:
        computed = datetime.fromisoformat(snapshot.computed_at)
    except (ValueError, TypeError):
        return None
    if computed.tzinfo is None:
        computed = computed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - computed).total_seconds() / 3600


def is_stale(snapshot: DomainStateSnapshot, *, max_age_hours: float = 2.0) -> bool:
    """A snapshot older than the freshness SLA (or with an unparseable/absent
    computed_at) is stale and is not used."""
    age = snapshot_age_hours(snapshot)
    return age is None or age > max_age_hours


def _snapshot_context(snapshot: DomainStateSnapshot, units: dict[str, str] | None = None) -> dict:
    """Compact, LLM-friendly view of the snapshot -- metric ids kept internal-
    only inputs; only the numbers the model needs to phrase the answer.

    The period, the change's baseline and each metric's unit are stated, not left
    to the model: live 2026-10-07 (MS3-e81cbc105a) it wrote "Period: 2026-09-30
    to 2026-10-06" for one day's values (it read the rolling-mean ``window``) and
    "Currency: $" for INR."""
    units = units or {}
    metrics = []
    for m in snapshot.metrics:
        change = m.period_delta_pct
        entry: dict = {
            "metric": m.metric_id.removeprefix("metric."),
            "value": m.value,
            "unit": units.get(m.metric_id),
            "change_pct_vs_prior_day": change,
            "avg_7d": m.rolling_mean_7d,
            "freshness": m.freshness,
        }
        if change is not None and abs(change) > P.MAX_REPORTABLE_PCT_CHANGE:
            # live 2026-10-15: "refunds +5,851.7%" was reported as the top priority. A percent off a near-zero base says
            # nothing: state the levels (value, avg_7d) instead of a percent.
            entry["change_pct_vs_prior_day"] = None
            entry["change_note"] = "the day before was near zero, so no percent change is meaningful: compare value with avg_7d"
        if m.observed_on and m.observed_on != snapshot.as_of:
            entry["note"] = f"the source had no value for {snapshot.as_of}; this is the latest one, from {m.observed_on}"
        if m.judgement:
            # The panel's verdict (weekday baseline, bad-direction rule, shape-aware move) replaces the older
            # whole-window anomaly block, so the agent and the Ops tab never disagree about what needs attention.
            j = m.judgement
            entry["status"] = m.status
            entry["verdict"] = j.get("reason")
            entry["anomaly"] = {"is_anomaly": j.get("is_anomaly"), "expected": j.get("expected"), "direction": j.get("direction")}
        elif m.anomaly:
            entry["anomaly"] = {
                "is_anomaly": m.anomaly.get("is_anomaly"),
                "expected": m.anomaly.get("expected"),
                "direction": m.anomaly.get("direction"),
            }
        metrics.append(entry)
    return {
        "as_of": snapshot.as_of,
        "period": snapshot.as_of,
        "values_are": f"one day ({snapshot.as_of}); change_pct_vs_prior_day compares it with the day before",
        "covers": f"the last complete day, {snapshot.as_of}: not today. For today, right now or any other period, call metrics_query instead.",
        "avg_7d_window": snapshot.window,
        "status": snapshot.status,
        "signals": snapshot.headline_signals,
        "metrics": metrics,
    }


def _units(runtime: SwarmRuntime, snapshot: DomainStateSnapshot) -> dict[str, str]:
    registry = getattr(runtime, "metrics", None)
    out: dict[str, str] = {}
    for m in snapshot.metrics:
        definition = registry.get(m.metric_id) if registry is not None else None
        if definition is not None and definition.unit:
            out[m.metric_id] = definition.unit
    return out

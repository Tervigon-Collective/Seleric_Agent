from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import StateNeed, StateRequest
from seleric_swarm.paths import repo_root
from seleric_swarm.services.domain_health.models import (
    DomainStateSnapshot,
    DomainStatus,
    ResolvedMetric,
)
from seleric_swarm.services.time_range import resolve_time_range

if TYPE_CHECKING:
    from seleric_swarm.services.business_state.facade import BusinessStateService
    from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore


class DomainHealthProfiles:
    """Loads config/domain_health_profiles.yaml (8 domains: commerce, finance,
    performance, attribution, funnel, product, customer, operations --
    inventory/procurement/technical stay excluded, no MCP module).
    """

    def __init__(self, config_path: str | Path = "config/domain_health_profiles.yaml") -> None:
        path = Path(config_path)
        if not path.is_absolute():
            path = repo_root() / path
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self._domains: dict[str, Any] = data.get("domains") or {}

    def get(self, domain: str) -> dict[str, Any]:
        block = self._domains.get(domain)
        if block is None:
            raise KeyError(f"Unknown domain_health domain: {domain}")
        return block


def _feature_value(state: Any, feature_id: str) -> float | None:
    feature = state.features.get(feature_id)
    return feature.value if feature else None


_RULE_COMPARATORS = {
    "period_delta_pct_below": (lambda value, threshold: value < threshold, "below"),
    "period_delta_pct_above": (lambda value, threshold: value > threshold, "above"),
}


def _headline_signals(rules: list[dict[str, Any]], resolved: list[ResolvedMetric]) -> list[str]:
    """Deterministic threshold checks only (03/04's Claim Gate requirement:
    the "why it's flagged" comes from code, not the LLM). Both directions of
    the one rule kind every domain config currently uses (period_delta_pct
    vs. a threshold) are implemented; add a new rule kind (e.g. anomaly-based)
    when a domain's config actually needs one.

    `period_delta_pct` (features.py) and the config's `threshold` are both
    already percent-scale (-15 means -15%), not a 0-1 fraction -- confirmed
    against live data, where a 0-1 threshold made every real drop look like
    a >1000% breach.
    """
    by_metric = {m.metric_id: m for m in resolved}
    signals: list[str] = []
    for rule in rules:
        rule_name = rule.get("rule")
        if not isinstance(rule_name, str):
            continue
        comparator = _RULE_COMPARATORS.get(rule_name)
        if comparator is None:
            continue
        breached, direction = comparator
        metric = by_metric.get(rule["metric_id"])
        value = metric.period_delta_pct if metric else None
        threshold = rule["threshold"]
        if value is not None and breached(value, threshold):
            signals.append(
                f"{rule['id']}: {rule['metric_id']} period_delta_pct {value:.1f}% "
                f"{direction} threshold {threshold:.1f}%"
            )
    return signals


def _windowed_point_delta_pct(current: float | None, previous: float | None) -> float | None:
    """cron-run-over-cron-run delta for `feature_class: windowed_point`
    metrics (customer/repeat_rate) -- these have no report_date axis, so
    BusinessStateService's daily-series features don't apply (06_DATA_
    VALIDATION_FINDINGS.md#4); the only valid "trend" is this snapshot's
    value vs. the previous snapshot's value for the same metric.
    """
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100


class DomainStateResolver:
    """Per-domain: calls BusinessStateService for each configured metric and
    assembles one DomainStateSnapshot. All 8 buildable domains
    (inventory/procurement/technical excluded, no MCP module) share this one
    resolver -- config-driven, no per-domain code (05_SPRINT_PLAN.md
    Sprint 3/4). Manual/callable trigger; scheduler.py wires it to cron.
    """

    def __init__(
        self,
        business_state: BusinessStateService,
        profiles: DomainHealthProfiles | None = None,
    ) -> None:
        self._business_state = business_state
        self._profiles = profiles or DomainHealthProfiles()

    async def resolve(
        self,
        domain: str,
        *,
        time_range: TimeRangeV1,
        brand_id: str = "20",
        store: SnapshotStore | None = None,
    ) -> DomainStateSnapshot:
        block = self._profiles.get(domain)
        agent_id = block.get("agent_id", f"{domain}_agent")
        # `time_range` is typically still unresolved (kind="relative",
        # e.g. "last_7d") at this point — get_metric_state resolves it
        # internally per metric but doesn't hand the concrete dates back, so
        # every snapshot's window/as_of used to be written as None/None.
        # Resolved here once, display-only; per-metric fetching below still
        # goes through facade.get_metric_state's own (per-metric-timezone)
        # resolution unchanged.
        resolved_range = resolve_time_range(time_range, "UTC", None)
        previous = await store.aget_latest(domain) if store else None
        previous_by_metric = {m.metric_id: m for m in previous.metrics} if previous else {}

        resolved: list[ResolvedMetric] = []
        query_ids: list[str] = []
        status: DomainStatus = "OK"
        for entry in block.get("metrics", []):
            metric_id = entry["metric_id"]
            windowed_point = entry.get("feature_class") == "windowed_point"
            feature_ids = entry.get("features") or []
            need: list[StateNeed] = ["actual"]
            if feature_ids and not windowed_point:
                need.append("features")
            request = StateRequest(
                metric_id=metric_id,
                time_range=time_range,
                dimensions={"brand_id": brand_id},
                agent_id=agent_id,
                need=need,
            )
            state = await self._business_state.get_metric_state(request)

            if state.status == "UNAVAILABLE":
                status = "UNAVAILABLE"
            elif state.status == "PARTIAL" and status == "OK":
                status = "DEGRADED"

            if windowed_point:
                period_delta_pct = _windowed_point_delta_pct(
                    state.actual, previous_by_metric.get(metric_id, ResolvedMetric(metric_id=metric_id)).value
                )
                rolling_mean_7d = None
            else:
                period_delta_pct = _feature_value(state, "period_delta_pct")
                rolling_mean_7d = _feature_value(state, "rolling_mean_7d")

            resolved.append(
                ResolvedMetric(
                    metric_id=metric_id,
                    value=state.actual,
                    period_delta_pct=period_delta_pct,
                    rolling_mean_7d=rolling_mean_7d,
                    direction_bad=state.direction_bad,
                    freshness=state.freshness,
                    quality_flags=state.quality_flags,
                )
            )
            query_id = state.provenance.get("query_id")
            if query_id:
                query_ids.append(str(query_id))

        return DomainStateSnapshot(
            domain=domain,
            brand_id=brand_id,
            as_of=resolved_range.end or datetime.now(UTC).date().isoformat(),
            computed_at=datetime.now(UTC).isoformat(),
            window={"start": resolved_range.start, "end": resolved_range.end},
            status=status,
            metrics=resolved,
            headline_signals=_headline_signals(block.get("health_signals") or [], resolved),
            provenance={"mcp_query_ids": query_ids, "profile_id": "domain_health/" + domain},
        )

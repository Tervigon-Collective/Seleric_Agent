from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import asyncio
import statistics

import yaml

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import StateNeed, StateRequest
from seleric_swarm.exploration.space import configured_scope_ids, default_brand_id
from seleric_swarm.paths import repo_root
from seleric_swarm.services.domain_health.judge import infer_shape, judge
from seleric_swarm.services.domain_health.models import (
    DomainStateSnapshot,
    DomainStatus,
    ResolvedMetric,
)
from seleric_swarm.services.domain_health.monitor_config import OverrideStore, merge_domain
from seleric_swarm.services.metrics import _MULTIPLE_HINT, is_percent_share_metric
from seleric_swarm.services.time_range import resolve_time_range

if TYPE_CHECKING:
    from seleric_swarm.services.business_state.facade import BusinessStateService
    from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore


MAX_PARALLEL_QUERIES = 5


class DomainHealthProfiles:
    """Loads config/domain_health_profiles.yaml (the shipped defaults) and, when given an ``OverrideStore``, lays the
    user's saved selection over it. Domains are whatever the file names: nothing else lists them.
    """

    def __init__(
        self, config_path: str | Path = "config/domain_health_profiles.yaml", overrides: OverrideStore | None = None
    ) -> None:
        path = Path(config_path)
        if not path.is_absolute():
            path = repo_root() / path
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self._domains: dict[str, Any] = data.get("domains") or {}
        self._headline: list[str] = data.get("headline_metrics") or []
        self._overrides = overrides

    @classmethod
    def live(cls) -> DomainHealthProfiles:
        """Defaults plus the overrides stored beside the snapshots (what the cron and the api both read)."""
        from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

        return cls(overrides=OverrideStore(SnapshotStore().base_dir))

    def domain_ids(self) -> list[str]:
        return list(self._domains)

    def get(self, domain: str) -> dict[str, Any]:
        block = self._domains.get(domain)
        if block is None:
            raise KeyError(f"Unknown domain_health domain: {domain}")
        return merge_domain(block, self._overrides.get(domain) if self._overrides else None)

    def defaults(self, domain: str) -> dict[str, Any]:
        block = self._domains.get(domain)
        if block is None:
            raise KeyError(f"Unknown domain_health domain: {domain}")
        return block

    def override_updated_at(self, domain: str) -> str | None:
        return self._overrides.updated_at(domain) if self._overrides else None

    def headline_metrics(self) -> list[str]:
        """The metrics the cross-domain ``business`` snapshot carries: the shipped list, minus what the user has taken
        out or switched off with its domain, plus what they have added (``headline: true`` on a selected metric)."""
        flags: dict[str, bool] = {}
        for domain in self._domains:
            block = self.get(domain)
            on = block.get("enabled", True)
            for entry in block.get("metrics", []):
                flags[entry["metric_id"]] = on and bool(entry.get("headline", entry["metric_id"] in self._headline))
        return [m for m in self._headline if flags.get(m, True)] + [m for m, v in flags.items() if v and m not in self._headline]


def display_kind(metric_id: str, unit: str | None, values: list[float] | None = None) -> str | None:
    """How a value is written. The catalogue calls a conversion rate, a margin and MER all "ratio"; only a share is
    shown as a percent. The codebase's own name rule (services.metrics) decides what it knows; for the rest (margins
    end in ``_pct``, which it does not know) the data decides: a ratio whose typical day sits within [-1, 1] is a share
    (the median, so one day of tiny sales and a wild margin does not turn it into a multiple)."""
    u = (unit or "").lower()
    if u == "ratio":
        if is_percent_share_metric(metric_id, unit):
            return "percent"
        if _MULTIPLE_HINT.search(metric_id):
            return "multiple"
        return "percent" if values and statistics.median(abs(v) for v in values) <= 1.0 else "multiple"
    return {"inr": "currency", "currency": "currency", "count": "count", "seconds": "seconds"}.get(u)


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
        # Each metric is one MCP query. Fetching them one after another made an 8-metric domain take 35 s and a full
        # 40-metric one minutes; unbounded would hammer the MCP. One cap shared by every domain this resolver serves.
        self._slots: asyncio.Semaphore | None = None

    @property
    def profiles(self) -> DomainHealthProfiles:
        return self._profiles

    def _timezone(self, block: dict[str, Any]) -> str:
        """The timezone the domain's metrics are resolved in (their registry entries)."""
        registry = getattr(getattr(self._business_state, "_runtime", None), "metrics", None)
        for entry in block.get("metrics", []):
            definition = registry.get(entry.get("catalogue_metric") or entry["metric_id"]) if registry is not None else None
            if definition is not None and getattr(definition, "timezone", None):
                return str(definition.timezone)
        return "UTC"

    def _definition(self, catalogue_id: str) -> Any:
        registry = getattr(getattr(self._business_state, "_runtime", None), "metrics", None)
        return registry.get(catalogue_id) if registry is not None else None

    @staticmethod
    def _polarity(entry: dict[str, Any], definition: Any, state_direction: str | None) -> str | None:
        """The way that is bad for this metric. None when nobody has said: the catalogue's silent default of "up" is
        not a statement, so a metric without an explicit direction is watched but never alerted."""
        override = entry.get("direction_bad")
        if override in ("up", "down"):
            return override
        if override == "none":
            return None
        raw = getattr(definition, "raw", None)
        if isinstance(raw, dict):
            return raw.get("direction_bad") if raw.get("direction_bad") in ("up", "down") else None
        return state_direction

    async def resolve(
        self,
        domain: str,
        *,
        time_range: TimeRangeV1,
        brand_id: str | None = None,
        store: SnapshotStore | None = None,
    ) -> DomainStateSnapshot:
        brand_id = brand_id or default_brand_id()
        scope_ids = configured_scope_ids()
        scope_dim = scope_ids[0] if scope_ids else ""
        block = self._profiles.get(domain)
        agent_id = block.get("agent_id", f"{domain}_agent")
        # In the metrics' own timezone: resolved in UTC, a snapshot computed between 00:00 and 05:30 IST was labelled
        # with the day before the one its values are for (live 2026-10-08).
        resolved_range = resolve_time_range(time_range, self._timezone(block), None)
        previous = await store.aget_latest(domain) if store else None
        previous_by_metric = {m.metric_id: m for m in previous.metrics} if previous else {}

        resolved: list[ResolvedMetric] = []
        query_ids: list[str] = []
        status: DomainStatus = "OK"
        unavailable = 0
        entries = block.get("metrics", [])
        requests = []
        for entry in entries:
            windowed_point = entry.get("feature_class") == "windowed_point"
            feature_ids = entry.get("features") or []
            # Every daily metric asks for the anomaly window (28d): the same single MCP call then also returns the
            # series the panel draws and the history the judge needs.
            need: list[StateNeed] = ["actual"]
            if feature_ids and not windowed_point:
                need.append("features")
            if not windowed_point:
                need.append("anomaly")
            requests.append(
                StateRequest(
                    metric_id=entry["metric_id"],
                    catalogue_metric_id=entry.get("catalogue_metric"),
                    filters=entry.get("filters") or {},
                    date_axis=entry.get("date_axis"),
                    time_range=time_range,
                    dimensions={scope_dim: brand_id} if scope_dim else {},
                    agent_id=agent_id,
                    need=need,
                )
            )
        if self._slots is None:
            self._slots = asyncio.Semaphore(MAX_PARALLEL_QUERIES)

        async def _fetch(request: StateRequest) -> Any:
            async with self._slots:  # type: ignore[union-attr]
                return await self._business_state.get_metric_state(request)

        states = await asyncio.gather(*[_fetch(r) for r in requests])
        for entry, state in zip(entries, states, strict=True):
            metric_id = entry["metric_id"]
            windowed_point = entry.get("feature_class") == "windowed_point"

            # One metric the catalogue cannot answer degrades the domain; it is UNAVAILABLE only when none answered.
            # A short history (SPARSE_HISTORY) is a property of a young metric, not a fault, so it does not degrade.
            if state.status == "UNAVAILABLE":
                unavailable += 1
            elif state.status == "PARTIAL" and status == "OK" and not (state.quality_flags and set(state.quality_flags) <= {"SPARSE_HISTORY"}):
                status = "DEGRADED"

            definition = self._definition(entry.get("catalogue_metric") or metric_id)
            unit = getattr(definition, "unit", None)
            series = [(p.ts, p.value) for p in (state.series or []) if p.value is not None]
            display = display_kind(entry.get("catalogue_metric") or metric_id, unit, [v for _, v in series])
            # A multiple (MER, ROAS) is a positive amount, not a share: judge it in percent, never in percentage points.
            judge_unit = "ratio" if display == "percent" else (None if unit == "ratio" else unit)
            polarity = self._polarity(entry, definition, state.direction_bad)
            judgement = None
            if windowed_point or not series:
                shape = infer_shape(judge_unit, [state.actual] if state.actual is not None else [])
            else:
                judgement = judge(series, unit=judge_unit, direction_bad=polarity, rule=entry.get("alert"), expect_day=resolved_range.end)
                shape = judgement.shape

            if windowed_point:
                period_delta_pct = _windowed_point_delta_pct(
                    state.actual, previous_by_metric.get(metric_id, ResolvedMetric(metric_id=metric_id)).value
                )
                rolling_mean_7d = None
                rolling_std_7d = None
            else:
                period_delta_pct = _feature_value(state, "period_delta_pct")
                rolling_mean_7d = _feature_value(state, "rolling_mean_7d")
                rolling_std_7d = _feature_value(state, "rolling_std_7d")
            # A percent change only means something on a positive additive base.
            if shape != "additive" and not windowed_point:
                period_delta_pct = None

            resolved.append(
                ResolvedMetric(
                    metric_id=metric_id,
                    value=state.actual,
                    period_delta_pct=period_delta_pct,
                    rolling_mean_7d=rolling_mean_7d,
                    rolling_std_7d=rolling_std_7d,
                    direction_bad=polarity,
                    freshness=state.freshness,
                    anomaly=state.anomaly,
                    quality_flags=state.quality_flags,
                    name=getattr(definition, "description", None),
                    label=entry.get("label"),
                    unit=unit,
                    display=display,
                    shape=shape,
                    series=[{"ts": ts, "value": v} for ts, v in series[-28:]],
                    judgement=judgement.as_dict() if judgement else None,
                    observed_on=series[-1][0][:10] if series else None,
                    status=judgement.status if judgement else "no_data",
                    polarity_known=polarity is not None,
                    rule=entry.get("alert"),
                )
            )
            query_id = state.provenance.get("query_id")
            if query_id:
                query_ids.append(str(query_id))

        if unavailable:
            status = "UNAVAILABLE" if unavailable == len(resolved) else "DEGRADED"
        judged = [
            f"{m.label or m.metric_id}: {m.judgement['reason']} (now {m.judgement['observed']:.4g}, "
            f"expected {m.judgement['expected']:.4g})"
            for m in resolved
            if m.status == "alert" and m.judgement
        ]
        return DomainStateSnapshot(
            domain=domain,
            title=block.get("title"),
            brand_id=brand_id,
            as_of=resolved_range.end or datetime.now(UTC).date().isoformat(),
            computed_at=datetime.now(UTC).isoformat(),
            window={"start": resolved_range.start, "end": resolved_range.end},
            status=status,
            metrics=resolved,
            headline_signals=[*judged, *_headline_signals(block.get("health_signals") or [], resolved)],
            provenance={
                "mcp_query_ids": query_ids,
                "profile_id": "domain_health/" + domain,
                "config": "custom" if block.get("custom") else "default",
                "config_version": block.get("version", 0),
            },
        )

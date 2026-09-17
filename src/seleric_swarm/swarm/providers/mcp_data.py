"""MCP-backed DataProvider — Coordinator never calls MCP; domain agents do.

Preferred path: DomainAgent → DataProvider → MCPGateway → server.
A domain either has live Seleric catalogue data or it has none (reported as
``missing``) — there is no canned-data fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.registry.agent_registry import AgentRegistry
from seleric_swarm.registry.provider_registry import ProviderRegistry
from seleric_swarm.services.business_state.detectors import RobustZScoreDetector
from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap
from seleric_swarm.services.mcp_query import (
    build_metrics_query_args,
    call_metrics_query,
    dimension_value,
    row_date,
    split_dimension_dict,
)
from seleric_swarm.services.measure import module_args, resolve_measure
from seleric_swarm.services.metrics import MetricDefinition, MetricRegistry
from seleric_swarm.swarm.domain.configs import build_domain_configs
from seleric_swarm.swarm.providers.base import (
    DataResult,
    DomainEvent,
    MetricReading,
    ProviderBundle,
)
from seleric_swarm.swarm.providers.provider_selection import ConfiguredAnomalyDetector
from seleric_swarm.swarm.providers.template import (
    TemplateAnomalyDetector,
    TemplateCausalEngine,
    TemplateForecaster,
    TemplateOptimizer,
    TemplateStatsEngine,
)

_MCP_QUERY_CONCURRENCY = 8


@dataclass
class McpFetchStats:
    """Per-mission counters for MCP hits vs live-data gaps (no secrets)."""

    mcp_attempts: int = 0
    mcp_hits: int = 0
    mcp_fallbacks: int = 0
    capabilities_used: list[str] = field(default_factory=list)
    fallback_reasons: list[str] = field(default_factory=list)
    # (metric_id, stale_catalogue_id, resolved_catalogue_id) — populated when
    # _resolve_measure auto-heals a stale registry entry via semantic search.
    stale_registry_subs: list[tuple[str, str, str]] = field(default_factory=list)

    def record_stale_sub(self, metric_id: str, stale: str, resolved: str) -> None:
        """Record a registry-to-catalogue mismatch that was auto-healed."""
        self.stale_registry_subs.append((metric_id, stale, resolved))

    def limitations(self) -> list[str]:
        lines: list[str] = []
        # Stale registry entries surface first — they are actionable operator signals.
        for metric_id, stale, resolved in self.stale_registry_subs:
            lines.append(
                f"Registry stale: {metric_id} catalogue_metric '{stale}' not found in live catalogue; "
                f"auto-resolved to '{resolved}'. Update metric_registry.yaml to silence this."
            )
        if self.mcp_hits:
            lines.append(
                f"MCP data path used for {self.mcp_hits} domain fetch(es): "
                + ", ".join(sorted(set(self.capabilities_used)) or ["(none)"])
            )
        if self.mcp_fallbacks:
            reason = "; ".join(self.fallback_reasons[:3]) or "capability unavailable or call failed"
            lines.append(f"Live MCP gaps ({self.mcp_fallbacks}x): {reason}")
        return lines


class EmptyDataProvider:
    """No live MCP coverage for this domain — returns nothing (never fixtures)."""

    def __init__(self, domain: str) -> None:
        self.domain = domain

    async def fetch(
        self,
        *,
        metric_ids: list[str],
        time_range: dict[str, Any],
        dimensions: dict[str, Any] | None = None,
        limit: int | None = None,
        sort: list[dict[str, Any]] | None = None,
    ) -> DataResult:
        del time_range, dimensions, limit, sort
        return DataResult(readings=[], events=[], missing=list(metric_ids), data_origin="MCP", synthetic=False)

    async def events(self, *, time_range: dict[str, Any]) -> list[DomainEvent]:
        del time_range
        return []


class HybridMcpDataProvider:
    """Live Seleric MCP for a domain module. Never returns fixture readings.

    Metrics are never hardcoded: for each requested ``metric_id`` we look up
    its ``MetricDefinition`` (config/metric_registry.yaml) and resolve the
    live catalogue measure id via ``seleric.catalogue_search_metrics``, then
    query it with ``seleric.metrics_query``.
    """

    def __init__(
        self,
        domain: str,
        *,
        mcp: MCPGateway,
        stats: McpFetchStats,
        metrics: MetricRegistry,
        agent_id: str,
        execution_mode: str = "production",
        bootstrap: CatalogueBootstrap | None = None,
    ) -> None:
        self.domain = domain
        self._mcp = mcp
        self._stats = stats
        self._metrics = metrics
        self._agent_id = agent_id
        self._execution_mode = execution_mode
        self._bootstrap = bootstrap
        self._measure_cache: dict[str, str | None] = {}

    def _empty(self, metric_ids: list[str]) -> DataResult:
        return DataResult(readings=[], events=[], missing=list(metric_ids), data_origin="MCP", synthetic=False)

    async def _resolve_measure(self, definition: MetricDefinition) -> str | None:
        return await resolve_measure(
            definition,
            mcp=self._mcp,
            agent_id=self._agent_id,
            bootstrap=self._bootstrap,
            metrics=self._metrics,
            cache=self._measure_cache,
            on_stale_sub=self._stats.record_stale_sub,
        )

    async def fetch(
        self,
        *,
        metric_ids: list[str],
        time_range: dict[str, Any],
        dimensions: dict[str, Any] | None = None,
        limit: int | None = None,
        sort: list[dict[str, Any]] | None = None,
    ) -> DataResult:
        want = list(metric_ids or [])
        if "seleric.metrics_query" not in self._mcp.capabilities:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append("seleric.metrics_query not registered")
            return self._empty(want)

        start = str(time_range.get("start") or time_range.get("end") or "")[:10]
        end = str(
            time_range.get("observation_end") or time_range.get("end") or time_range.get("start") or ""
        )[:10]
        if not start or not end:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append("seleric.metrics_query: missing date in time_range")
            return self._empty(want)

        breakdown, extra_filters = split_dimension_dict(dimensions)
        missing: list[str] = []
        resolved: list[tuple[str, MetricDefinition, str]] = []
        for metric_id in want:
            definition = self._metrics.get(metric_id)
            if definition is None:
                missing.append(metric_id)
                continue
            measure = await self._resolve_measure(definition)
            if measure is None:
                self._stats.mcp_fallbacks += 1
                cat_hint = definition.catalogue_metric or "(no catalogue_metric hint)"
                self._stats.fallback_reasons.append(
                    f"{metric_id}: catalogue measure '{cat_hint}' not found "
                    "— update metric_registry.yaml or add a catalogue_metric hint"
                )
                missing.append(metric_id)
                continue
            resolved.append((metric_id, definition, measure))

        sem = asyncio.Semaphore(_MCP_QUERY_CONCURRENCY)

        async def _query_metric(
            metric_id: str, definition: MetricDefinition, measure: str
        ) -> tuple[str, MetricDefinition, str, dict[str, Any]]:
            extra = module_args(definition)
            args = build_metrics_query_args(
                measure=measure,
                start=start,
                end=end,
                dimensions=breakdown or None,
                filters=extra_filters or None,
                limit=limit,
                sort=sort or ([{"field": measure, "direction": "desc"}] if breakdown else None),
                compare_period=None if breakdown else "previous_period",
                module=extra["module"] if extra else ...,
            )
            async with sem:
                self._stats.mcp_attempts += 1
                return metric_id, definition, measure, await call_metrics_query(
                    self._mcp, agent_id=self._agent_id, arguments=args
                )

        gathered = await asyncio.gather(*[_query_metric(*item) for item in resolved]) if resolved else []
        readings: list[MetricReading] = []
        for metric_id, definition, measure, result in gathered:
            rows = result.get("rows") or []
            if result.get("error") or not rows:
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(
                    f"{metric_id}: {result['error']}" if result.get("error") else f"{metric_id}: no data for {start}..{end}"
                )
                missing.append(metric_id)
                continue
            source_label = f"seleric_mcp.{(result.get('provenance') or {}).get('cube_view', measure)}"
            baseline = None
            compare_rows = result.get("compare_rows") or []
            if compare_rows and compare_rows[0].get(measure) is not None:
                baseline = float(compare_rows[0][measure])
            emitted = False
            seen: set[tuple[tuple[str, Any], ...]] = set()
            for row in rows:
                raw_value = row.get(measure)
                if raw_value is None:
                    continue
                dims: dict[str, Any] = {}
                if breakdown:
                    dims = {d: dimension_value(row, d) for d in breakdown}
                    dims = {k: v for k, v in dims.items() if v is not None}
                    key = tuple(sorted(dims.items()))
                    if key in seen:
                        continue
                    seen.add(key)
                readings.append(
                    MetricReading(
                        metric_id=metric_id,
                        value=float(raw_value),
                        baseline=None if breakdown else baseline,
                        unit=getattr(definition, "unit", None),
                        direction_bad=getattr(definition, "direction_bad", "up"),
                        dimensions=dims,
                        data_origin="MCP",
                        synthetic=False,
                        source=source_label,
                        source_metadata={
                            **(result.get("provenance") or {}),
                            "tool": "seleric.metrics_query",
                            "tool_version": str(result.get("tool_version") or "1"),
                            "query": {
                                "measure": measure,
                                "start": start,
                                "end": end,
                                "dimensions": breakdown,
                                "filters": extra_filters,
                            },
                        },
                    )
                )
                emitted = True
                if not breakdown:
                    break
            if not emitted:
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(f"{metric_id}: measure key absent from row")
                missing.append(metric_id)

        if not readings:
            return self._empty(want)

        self._stats.mcp_hits += 1
        self._stats.capabilities_used.append("seleric.metrics_query")
        return DataResult(readings=readings, events=[], missing=missing, data_origin="MCP", synthetic=False)

    async def fetch_series(
        self,
        *,
        metric_ids: list[str],
        time_range: dict[str, Any],
        max_days: int = 60,
        min_rows: int = 8,
    ) -> Any:
        """Daily series for DoWhy: one ``metrics_query`` per metric with ``granularity=day``.

        Returns a pandas DataFrame indexed by date, or ``None`` if the window is
        shorter than ``min_rows``, longer than ``max_days``, or too sparse.
        """
        from datetime import date

        import pandas as pd

        start_s = str(time_range.get("start") or time_range.get("end") or "")[:10]
        end_s = str(time_range.get("end") or time_range.get("start") or "")[:10]
        if not start_s or not end_s:
            return None
        try:
            start = date.fromisoformat(start_s)
            end = date.fromisoformat(end_s)
        except ValueError:
            return None
        if end < start:
            start, end = end, start
        n_days = (end - start).days + 1
        if n_days < min_rows or n_days > max_days:
            return None

        jobs: list[tuple[str, str, dict[str, Any]]] = []
        for metric_id in metric_ids:
            definition = self._metrics.get(metric_id)
            if definition is None:
                continue
            measure = await self._resolve_measure(definition)
            if measure is None:
                continue
            extra = module_args(definition)
            jobs.append((metric_id, measure, extra))

        sem = asyncio.Semaphore(_MCP_QUERY_CONCURRENCY)

        async def _query_metric(
            metric_id: str, measure: str, extra: dict[str, Any]
        ) -> tuple[str, dict[str, float]]:
            args = build_metrics_query_args(
                measure=measure,
                start=start.isoformat(),
                end=end.isoformat(),
                grain="day",
                module=extra["module"] if extra else ...,
            )
            async with sem:
                self._stats.mcp_attempts += 1
                result = await call_metrics_query(self._mcp, agent_id=self._agent_id, arguments=args)
            day_values: dict[str, float] = {}
            if result.get("error"):
                return metric_id, day_values
            for row in result.get("rows") or []:
                ts = row_date(row)
                raw = row.get(measure)
                if ts is None or raw is None:
                    continue
                day_values[ts] = float(raw)
            return metric_id, day_values

        gathered = await asyncio.gather(*[_query_metric(*job) for job in jobs]) if jobs else []
        columns: dict[str, dict[str, float]] = {
            metric_id: day_values
            for metric_id, day_values in gathered
            if len(day_values) >= min_rows
        }
        if not columns:
            return None
        frame = pd.DataFrame(columns)
        frame = frame.dropna(how="any")
        if len(frame) < min_rows:
            return None
        return frame

    async def events(self, *, time_range: dict[str, Any]) -> list[DomainEvent]:
        del time_range
        return []


def _data_providers(
    *,
    mcp: MCPGateway | None,
    execution_mode: str,
    metrics: MetricRegistry,
    agents: AgentRegistry | None,
    bootstrap: CatalogueBootstrap | None,
) -> tuple[dict[str, Any], McpFetchStats]:
    stats = McpFetchStats()
    data: dict[str, Any] = {}
    for cfg in build_domain_configs(metrics, agents).values():
        d = cfg.domain
        if mcp is not None and cfg.seleric_module:
            data[d] = HybridMcpDataProvider(
                d,
                mcp=mcp,
                stats=stats,
                metrics=metrics,
                agent_id=cfg.agent_id,
                execution_mode=execution_mode,
                bootstrap=bootstrap,
            )
        else:
            data[d] = EmptyDataProvider(d)
    return data, stats


def data_only_bundle(
    *,
    mcp: MCPGateway | None = None,
    execution_mode: str = "production",
    metrics: MetricRegistry,
    agents: AgentRegistry | None = None,
    bootstrap: CatalogueBootstrap | None = None,
) -> tuple[ProviderBundle, McpFetchStats]:
    """MCP/empty data providers plus cheap Template* seams. Lookup observe uses this."""
    data, stats = _data_providers(
        mcp=mcp,
        execution_mode=execution_mode,
        metrics=metrics,
        agents=agents,
        bootstrap=bootstrap,
    )
    return (
        ProviderBundle(
            data=data,
            anomaly=TemplateAnomalyDetector(),
            causal=TemplateCausalEngine(),
            forecaster=TemplateForecaster(),
            optimizer=TemplateOptimizer(),
            stats=TemplateStatsEngine(),
        ),
        stats,
    )


def build_hybrid_bundle(
    *,
    mcp: MCPGateway | None = None,
    execution_mode: str = "production",
    metrics: MetricRegistry,
    agents: AgentRegistry | None = None,
    bootstrap: CatalogueBootstrap | None = None,
    business_state: Any | None = None,
    provider_registry: ProviderRegistry | None = None,
) -> tuple[ProviderBundle, McpFetchStats]:
    """Build providers for a live mission: live MCP for domains with a
    seleric_module, no data otherwise.

    ``bootstrap`` is the shared ``CatalogueBootstrap`` instance from
    ``SwarmRuntime``.  When provided, ``_resolve_measure`` can check the
    live catalogue cache (Step 0) before making any MCP calls, eliminating
    the per-metric ``catalogue_get_metric`` round-trip on the hot path.

    ``business_state`` (``runtime.business_state``, typed loosely here to
    avoid a hard import-time dependency on ``SwarmRuntime``) and
    ``provider_registry`` are optional so every existing caller/test that
    doesn't pass them keeps getting pure ``TemplateAnomalyDetector`` behavior
    -- config-driven selection (Sprint 2.5) only activates once both a
    registry override and a live ``business_state`` are present.
    """
    data, stats = _data_providers(
        mcp=mcp,
        execution_mode=execution_mode,
        metrics=metrics,
        agents=agents,
        bootstrap=bootstrap,
    )
    # Data is MCP/empty; intelligence seams stay Template* by default so
    # specialists (anomaly / lightweight diagnostic / prediction / skeptic)
    # never see None. Anomaly is the one seam config can redirect per
    # metric/domain (docs/features/business-state-service/05_SPRINT_PLAN.md
    # Sprint 2.5) -- causal/forecast/optimizer/stats stay Template until
    # their own BusinessStateService strategies exist.
    registry = provider_registry or ProviderRegistry()
    anomaly_detector = ConfiguredAnomalyDetector(
        registry=registry,
        metrics=metrics,
        template=TemplateAnomalyDetector(),
        robust_zscore=RobustZScoreDetector(business_state) if business_state is not None else None,
    )
    bundle = ProviderBundle(
        data=data,
        anomaly=anomaly_detector,
        causal=TemplateCausalEngine(),
        forecaster=TemplateForecaster(),
        optimizer=TemplateOptimizer(),
        stats=TemplateStatsEngine(),
    )
    return bundle, stats

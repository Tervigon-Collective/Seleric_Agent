"""MCP-backed DataProvider — Coordinator never calls MCP; domain agents do.

Preferred path: DomainAgent → DataProvider → MCPGateway → server.
No fixture fallback: in production/staging, a domain either has live Seleric
catalogue data or it has none (reported as ``missing``). Fixture mode is a
separate, explicit offline path (build_fixture_bundle) never reachable from
a live mission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.registry.agent_registry import AgentRegistry
from seleric_swarm.services.metrics import MetricDefinition, MetricRegistry
from seleric_swarm.swarm.domain.configs import build_domain_configs
from seleric_swarm.swarm.providers.base import (
    DataResult,
    DomainEvent,
    MetricReading,
    ProviderBundle,
)
from seleric_swarm.swarm.providers.fixtures import (
    TemplateAnomalyDetector,
    TemplateCausalEngine,
    TemplateForecaster,
    TemplateOptimizer,
    TemplateStatsEngine,
    load_scenario,
)


@dataclass
class McpFetchStats:
    """Per-mission counters for MCP hits vs live-data gaps (no secrets)."""

    mcp_attempts: int = 0
    mcp_hits: int = 0
    mcp_fallbacks: int = 0
    capabilities_used: list[str] = field(default_factory=list)
    fallback_reasons: list[str] = field(default_factory=list)

    def limitations(self) -> list[str]:
        lines: list[str] = []
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
    ) -> DataResult:
        del time_range, dimensions
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
    ) -> None:
        self.domain = domain
        self._mcp = mcp
        self._stats = stats
        self._metrics = metrics
        self._agent_id = agent_id
        self._execution_mode = execution_mode
        self._measure_cache: dict[str, str | None] = {}

    def _empty(self, metric_ids: list[str]) -> DataResult:
        return DataResult(readings=[], events=[], missing=list(metric_ids), data_origin="MCP", synthetic=False)

    async def _resolve_measure(self, definition: MetricDefinition) -> str | None:
        """Resolve the live catalogue measure id for a registry metric (cached)."""
        if definition.id in self._measure_cache:
            return self._measure_cache[definition.id]
        preferred = definition.catalogue_metric
        args: dict[str, Any] = {"query": preferred}
        if "seleric_module" in definition.raw:
            args["module"] = definition.seleric_module
        try:
            result = await self._mcp.call(
                agent_id=self._agent_id,
                capability="seleric.catalogue_search_metrics",
                arguments=args,
            )
        except Exception:
            self._measure_cache[definition.id] = None
            return None
        matches = result.get("matches") or []
        resolved = preferred if any(m.get("id") == preferred for m in matches) else None
        if resolved is None and matches:
            resolved = matches[0].get("id")
        if resolved is None:
            resolved = preferred  # registry already named a catalogue id
        self._measure_cache[definition.id] = resolved
        return resolved

    async def fetch(
        self,
        *,
        metric_ids: list[str],
        time_range: dict[str, Any],
        dimensions: dict[str, Any] | None = None,
    ) -> DataResult:
        del dimensions  # ponytail: MCP dimensions later
        want = list(metric_ids or [])
        if "seleric.metrics_query" not in self._mcp.capabilities:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append("seleric.metrics_query not registered")
            return self._empty(want)

        # Prefer the mission's scripted observation end (single-day MCP fetch
        # anchored to the investigated window) over a client as_of that only
        # widened the reported range — see resolve_mission_time_range.
        start = str(time_range.get("start") or time_range.get("end") or "")[:10]
        end = str(
            time_range.get("observation_end") or time_range.get("end") or time_range.get("start") or ""
        )[:10]
        if not start or not end:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append("seleric.metrics_query: missing date in time_range")
            return self._empty(want)

        values: dict[str, float] = {}
        baselines: dict[str, float] = {}
        units: dict[str, str | None] = {}
        source_label = "seleric.metrics_query"
        missing: list[str] = []
        for metric_id in want:
            definition = self._metrics.get(metric_id)
            if definition is None:
                missing.append(metric_id)
                continue
            measure = await self._resolve_measure(definition)
            if measure is None:
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(f"{metric_id}: no catalogue measure resolved")
                missing.append(metric_id)
                continue
            args: dict[str, Any] = {
                "measures": [measure],
                "time_range": {"start": start, "end": end},
                "compare_period": "previous_period",
            }
            if "seleric_module" in definition.raw:
                args["module"] = definition.seleric_module
            self._stats.mcp_attempts += 1
            try:
                result = await self._mcp.call(
                    agent_id=self._agent_id, capability="seleric.metrics_query", arguments=args
                )
            except Exception as exc:
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(f"{metric_id}: {type(exc).__name__}")
                missing.append(metric_id)
                continue
            rows = result.get("rows") or []
            if result.get("error") or not rows:
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(f"{metric_id}: empty result for {start}..{end}")
                missing.append(metric_id)
                continue
            raw_value = rows[0].get(measure)
            if raw_value is None:
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(f"{metric_id}: measure missing from row")
                missing.append(metric_id)
                continue
            values[metric_id] = float(raw_value)
            units[metric_id] = definition.unit
            compare_rows = result.get("compare_rows") or []
            if compare_rows:
                raw_baseline = compare_rows[0].get(measure)
                if raw_baseline is not None:
                    baselines[metric_id] = float(raw_baseline)
            source_label = f"seleric_mcp.{(result.get('provenance') or {}).get('cube_view', measure)}"

        if not values:
            return self._empty(want)

        self._stats.mcp_hits += 1
        self._stats.capabilities_used.append("seleric.metrics_query")
        readings = [
            MetricReading(
                metric_id=mid,
                value=value,
                baseline=baselines.get(mid),
                unit=units.get(mid),
                direction_bad="up",
                dimensions={},
                data_origin="MCP",
                synthetic=False,
                source=source_label,
            )
            for mid, value in values.items()
        ]
        return DataResult(readings=readings, events=[], missing=missing, data_origin="MCP", synthetic=False)

    async def events(self, *, time_range: dict[str, Any]) -> list[DomainEvent]:
        del time_range
        return []


def build_hybrid_bundle(
    scenario_id: str,
    *,
    mcp: MCPGateway | None = None,
    execution_mode: str = "production",
    metrics: MetricRegistry,
    agents: AgentRegistry | None = None,
) -> tuple[ProviderBundle, McpFetchStats]:
    """Build providers for a live mission: live MCP for domains with a
    seleric_module, no data otherwise. ``scenario_id`` only supplies the
    causal/forecast template engines' stand-in truth until real causal
    inference / forecasting models replace them — never metric data.
    """
    scenario = load_scenario(scenario_id)
    stats = McpFetchStats()
    domain_cfgs = build_domain_configs(metrics, agents)
    data: dict[str, Any] = {}
    for cfg in domain_cfgs.values():
        d = cfg.domain
        if mcp is not None and cfg.seleric_module:
            data[d] = HybridMcpDataProvider(
                d,
                mcp=mcp,
                stats=stats,
                metrics=metrics,
                agent_id=cfg.agent_id,
                execution_mode=execution_mode,
            )
        else:
            data[d] = EmptyDataProvider(d)
    bundle = ProviderBundle(
        data=data,
        anomaly=TemplateAnomalyDetector(),
        causal=TemplateCausalEngine(scenario),
        forecaster=TemplateForecaster(scenario),
        optimizer=TemplateOptimizer(),
        stats=TemplateStatsEngine(scenario),
    )
    return bundle, stats

"""MCP-backed DataProvider — Coordinator never calls MCP; domain agents do.

Preferred path: DomainAgent → DataProvider → MCPGateway → server.
Falls back to the fixture provider when the capability is unavailable or the
call fails, and records provenance so synthesis can surface limitations.
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
    FixtureDataProvider,
    TemplateAnomalyDetector,
    TemplateCausalEngine,
    TemplateForecaster,
    TemplateOptimizer,
    TemplateStatsEngine,
    load_scenario,
)


@dataclass
class McpFetchStats:
    """Per-mission counters for MCP vs fixture fallback (no secrets)."""

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
            lines.append(f"MCP fallback to fixture providers ({self.mcp_fallbacks}x): {reason}")
        return lines


class HybridMcpDataProvider:
    """Prefer the live Seleric MCP catalogue for this domain's module; else fixture data.

    Metrics are never hardcoded: for each requested ``metric_id`` we look up
    its ``MetricDefinition`` (config/metric_registry.yaml) and resolve the
    live catalogue measure id via ``seleric.catalogue_search_metrics`` (same
    pattern as agents/intelligence/observer.py), then query it with
    ``seleric.metrics_query``.
    """

    def __init__(
        self,
        domain: str,
        *,
        mcp: MCPGateway,
        fallback: FixtureDataProvider,
        stats: McpFetchStats,
        metrics: MetricRegistry,
        agent_id: str,
        execution_mode: str = "fixture",
    ) -> None:
        self.domain = domain
        self._mcp = mcp
        self._fallback = fallback
        self._stats = stats
        self._metrics = metrics
        self._agent_id = agent_id
        self._execution_mode = execution_mode
        self._measure_cache: dict[str, str | None] = {}

    async def _resolve_measure(self, definition: MetricDefinition) -> str | None:
        """Resolve the live catalogue measure id for a registry metric (cached)."""
        cached = self._measure_cache.get(definition.id)
        if definition.id in self._measure_cache:
            return cached
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
        base = await self._fallback.fetch(
            metric_ids=metric_ids, time_range=time_range, dimensions=dimensions
        )
        if self._execution_mode == "fixture":
            return base
        if "seleric.metrics_query" not in self._mcp.capabilities:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append("seleric.metrics_query not registered")
            return base

        # Prefer the scenario's scripted observation end (single-day MCP fetch
        # anchored to the mission's investigated window) over a client as_of
        # that only widened the reported range — see resolve_mission_time_range.
        start = str(time_range.get("start") or time_range.get("end") or "")[:10]
        end = str(
            time_range.get("observation_end") or time_range.get("end") or time_range.get("start") or ""
        )[:10]
        if not start or not end:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append("seleric.metrics_query: missing date in time_range")
            return base

        want = metric_ids or [r.metric_id for r in base.readings if not r.dimensions]
        values: dict[str, float] = {}
        baselines: dict[str, float] = {}
        source_label = "seleric.metrics_query"
        for metric_id in want:
            definition = self._metrics.get(metric_id)
            if definition is None:
                continue
            measure = await self._resolve_measure(definition)
            if measure is None:
                self._stats.fallback_reasons.append(f"{metric_id}: no catalogue measure resolved")
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
                continue
            rows = result.get("rows") or []
            if result.get("error") or not rows:
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(f"{metric_id}: empty result for {start}..{end}")
                continue
            raw_value = rows[0].get(measure)
            if raw_value is None:
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(f"{metric_id}: measure missing from row")
                continue
            values[metric_id] = float(raw_value)
            # Real baseline from the same live query (previous period), not the
            # fixture's — mixing a live value with a synthetic baseline produces
            # nonsensical change_pct and corrupts anomaly detection.
            compare_rows = result.get("compare_rows") or []
            if compare_rows:
                raw_baseline = compare_rows[0].get(measure)
                if raw_baseline is not None:
                    baselines[metric_id] = float(raw_baseline)
            source_label = f"seleric_mcp.{(result.get('provenance') or {}).get('cube_view', measure)}"

        if not values:
            self._stats.mcp_fallbacks += 1
            if not self._stats.fallback_reasons:
                self._stats.fallback_reasons.append("seleric.metrics_query: no live values")
            return base

        self._stats.mcp_hits += 1
        self._stats.capabilities_used.append("seleric.metrics_query")
        origin = "MCP"
        source = source_label

        by_id = {r.metric_id: r for r in base.readings if not r.dimensions}
        merged: list[MetricReading] = []
        seen: set[str] = set()
        for mid, value in values.items():
            prev = by_id.get(mid)
            baseline = baselines.get(mid)
            definition = self._metrics.get(mid)
            unit = (definition.unit if definition else None) or (prev.unit if prev else None)
            direction = prev.direction_bad if prev else "up"
            if baseline is None:
                self._stats.fallback_reasons.append(f"{mid}: no live baseline")
            merged.append(
                MetricReading(
                    metric_id=mid,
                    value=float(value) if value is not None else None,
                    baseline=baseline,
                    unit=unit,
                    direction_bad=direction,
                    dimensions={},
                    data_origin=origin,
                    synthetic=False,
                    source=source,
                )
            )
            seen.add(mid)
        # Anything MCP couldn't supply (not requested, resolution failed, empty
        # result) stays on the fixture reading — production degrades gracefully
        # instead of leaving holes in the answer.
        for r in base.readings:
            key = r.metric_id if not r.dimensions else f"{r.metric_id}:{r.dimensions}"
            if r.metric_id in seen and not r.dimensions:
                continue
            merged.append(r)
            seen.add(str(key))

        return DataResult(
            readings=merged,
            events=base.events,
            missing=base.missing,
            data_origin=origin,
            synthetic=False,
        )

    async def events(self, *, time_range: dict[str, Any]) -> list[DomainEvent]:
        return await self._fallback.events(time_range=time_range)


def build_hybrid_bundle(
    scenario_id: str,
    *,
    mcp: MCPGateway | None = None,
    execution_mode: str = "fixture",
    metrics: MetricRegistry,
    agents: AgentRegistry | None = None,
) -> tuple[ProviderBundle, McpFetchStats]:
    """Build a ProviderBundle that prefers MCP for every domain with a seleric_module."""
    scenario = load_scenario(scenario_id)
    domains = list(scenario.get("domains", {}))
    stats = McpFetchStats()
    mode = execution_mode if execution_mode in {"production", "staging", "fixture"} else "fixture"
    domain_cfgs = {c.domain: c for c in build_domain_configs(metrics, agents).values()}
    data: dict[str, Any] = {}
    for d in domains:
        fixture = FixtureDataProvider(d, scenario)
        cfg = domain_cfgs.get(d)
        if mcp is not None and mode != "fixture" and cfg and cfg.seleric_module:
            data[d] = HybridMcpDataProvider(
                d,
                mcp=mcp,
                fallback=fixture,
                stats=stats,
                metrics=metrics,
                agent_id=cfg.agent_id,
                execution_mode=mode,
            )
        else:
            data[d] = fixture
    bundle = ProviderBundle(
        data=data,
        anomaly=TemplateAnomalyDetector(),
        causal=TemplateCausalEngine(scenario),
        forecaster=TemplateForecaster(scenario),
        optimizer=TemplateOptimizer(),
        stats=TemplateStatsEngine(scenario),
    )
    return bundle, stats

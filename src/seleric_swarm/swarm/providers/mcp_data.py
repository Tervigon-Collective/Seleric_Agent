"""MCP-backed DataProvider — Coordinator never calls MCP; domain agents do.

Preferred path: DomainAgent → DataProvider → MCPGateway → server.
Falls back to the fixture provider when the capability is unavailable or the
call fails, and records provenance so synthesis can surface limitations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.protocols.mcp.gateway import MCPGateway
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

# Domain → (agent_id for allowlist, MCP capability, metrics the fixture MCP serves)
DOMAIN_MCP_ROUTES: dict[str, tuple[str, str, frozenset[str]]] = {
    "commerce": ("commerce_agent", "commerce.daily_sales", frozenset({"metric.net_sales", "metric.gross_sales"})),
    "performance": ("performance_agent", "performance.daily_cac", frozenset({"metric.cac"})),
}


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
    """Prefer MCPGateway for known domain capabilities; else fixture scenario data."""

    def __init__(
        self,
        domain: str,
        *,
        mcp: MCPGateway,
        fallback: FixtureDataProvider,
        stats: McpFetchStats,
        execution_mode: str = "fixture",
    ) -> None:
        self.domain = domain
        self._mcp = mcp
        self._fallback = fallback
        self._stats = stats
        self._execution_mode = execution_mode
        route = DOMAIN_MCP_ROUTES.get(domain)
        self._agent_id = route[0] if route else f"{domain}_agent"
        self._capability = route[1] if route else None
        self._mcp_metrics = route[2] if route else frozenset()

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
        if self._execution_mode == "fixture" or not self._capability:
            return base

        if self._capability not in self._mcp.capabilities:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append(f"{self._capability} not registered")
            return base

        want = [m for m in (metric_ids or list(self._mcp_metrics)) if m in self._mcp_metrics]
        if not want:
            return base

        day = str(
            time_range.get("observation_end")
            or time_range.get("end")
            or time_range.get("start")
            or ""
        )[:10]
        if not day:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append(f"{self._capability}: missing date in time_range")
            return base

        self._stats.mcp_attempts += 1
        try:
            raw = await self._mcp.call(
                agent_id=self._agent_id,
                capability=self._capability,
                arguments={"date": day, "metrics": want},
            )
        except Exception as exc:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append(f"{self._capability}: {type(exc).__name__}")
            return base

        values = dict((raw or {}).get("metrics") or {})
        if not values:
            self._stats.mcp_fallbacks += 1
            self._stats.fallback_reasons.append(f"{self._capability}: empty metrics for {day}")
            return base

        self._stats.mcp_hits += 1
        self._stats.capabilities_used.append(self._capability)
        # Fixture MCP transports remain synthetic; live streamable_http would set False.
        transport_synthetic = not str((raw or {}).get("source") or "").startswith("seleric.")
        origin = "MCP"
        source = str((raw or {}).get("source") or self._capability)
        if transport_synthetic and not source.startswith("fixture."):
            source = f"mcp_fixture.{self._capability}"

        by_id = {r.metric_id: r for r in base.readings if not r.dimensions}
        merged: list[MetricReading] = []
        seen: set[str] = set()
        for mid, value in values.items():
            prev = by_id.get(mid)
            merged.append(
                MetricReading(
                    metric_id=mid,
                    value=float(value) if value is not None else None,
                    baseline=prev.baseline if prev else None,
                    unit=prev.unit if prev else None,
                    direction_bad=prev.direction_bad if prev else "up",
                    dimensions={},
                    data_origin=origin,
                    synthetic=transport_synthetic,
                    source=source,
                )
            )
            seen.add(mid)
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
            synthetic=transport_synthetic and base.synthetic,
        )

    async def events(self, *, time_range: dict[str, Any]) -> list[DomainEvent]:
        return await self._fallback.events(time_range=time_range)


def build_hybrid_bundle(
    scenario_id: str,
    *,
    mcp: MCPGateway | None = None,
    execution_mode: str = "fixture",
) -> tuple[ProviderBundle, McpFetchStats]:
    """Build a ProviderBundle that prefers MCP for commerce/performance when mode ≠ fixture."""
    scenario = load_scenario(scenario_id)
    domains = list(scenario.get("domains", {}))
    stats = McpFetchStats()
    mode = execution_mode if execution_mode in {"production", "staging", "fixture"} else "fixture"
    data: dict[str, Any] = {}
    for d in domains:
        fixture = FixtureDataProvider(d, scenario)
        if mcp is not None and mode != "fixture" and d in DOMAIN_MCP_ROUTES:
            data[d] = HybridMcpDataProvider(
                d, mcp=mcp, fallback=fixture, stats=stats, execution_mode=mode
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

"""MCP-backed DataProvider — Coordinator never calls MCP; domain agents do.

Preferred path: DomainAgent → DataProvider → MCPGateway → server.
A domain either has live Seleric catalogue data or it has none (reported as
``missing``) — there is no canned-data fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.registry.agent_registry import AgentRegistry
from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap
from seleric_swarm.services.metrics import MetricDefinition, MetricRegistry
from seleric_swarm.swarm.domain.configs import build_domain_configs
from seleric_swarm.swarm.providers.base import (
    DataResult,
    DomainEvent,
    MetricReading,
    ProviderBundle,
)
from seleric_swarm.swarm.providers.template import (
    TemplateAnomalyDetector,
    TemplateCausalEngine,
    TemplateForecaster,
    TemplateOptimizer,
    TemplateStatsEngine,
)


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


def _measure_keywords_overlap(definition: MetricDefinition, candidate_id: str) -> bool:
    """Guard: the semantic-fallback candidate must share at least one meaningful
    token with the registry metric's own ID tokens.

    This prevents the worst class of substitution errors — where the catalogue's
    NLP finds a distantly-related metric because the registry description is
    vague or the metric name is common in unrelated contexts.

    Examples (verified against the live catalogue):
      metric.return_rate {"return","rate"} ∩ total_orders {"total","orders"} = {} → REJECT ✓
      metric.cac         {"cac"} — handled by Step 1 (catalogue_get_metric); Step 2 never reached ✓
      metric.sessions    {"sessions"} ∩ web_sessions {"web","sessions"} = {"sessions"} → ACCEPT ✓
      metric.spend       {"spend"} ∩ amazon_ads_spend {"amazon","ads","spend"} = {"spend"} → ACCEPT ✓

    Only uses ID tokens (not description tokens) for precision — descriptions
    intentionally contain words like "orders", "cost", "rate" that appear in
    many unrelated catalogue metric IDs.
    """
    skip = {"metric"}
    id_tokens = {
        t.lower()
        for t in re.split(r"[._]", definition.id)
        if len(t) > 2 and t.lower() not in skip
    }
    candidate_tokens = {t.lower() for t in candidate_id.split("_") if len(t) > 2}
    return bool(id_tokens & candidate_tokens)


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
        """Resolve the live catalogue measure ID for a registry metric (cached).

        Two-step resolution — prevents stale registry entries from silently
        producing wrong or empty MCP results:

        Step 1 — Direct ID lookup via ``seleric.catalogue_get_metric``.
            Unlike catalogue_search_metrics, this is a deterministic exact-ID
            lookup: it either confirms the measure exists or returns an error.
            This fixes a critical bug in the previous approach where semantic
            search (catalogue_search_metrics(query=id)) missed short/acronym
            IDs like "cac", "orders", "total_ad_spend" because fuzzy search
            has no guarantee of returning an exact-ID match for the query.

        Step 2 — Semantic fallback with keyword-overlap guard.
            When the exact ID is absent, search by the metric's *description*
            to find a current equivalent.  A keyword-overlap guard filters
            out semantically-adjacent-but-wrong candidates
            (e.g. "return_rate" must not resolve to "total_orders").
            The substitution is recorded in McpFetchStats so the operator can
            update metric_registry.yaml to silence the warning.

        Returns None when truly unresolvable — never returns the stale preferred
        ID, which would send a phantom measure to metrics_query and produce a
        misleading "no data for period" error that hides the real problem.
        """
        if definition.id in self._measure_cache:
            return self._measure_cache[definition.id]

        preferred = definition.catalogue_metric

        # Step 0: Bootstrap cache — O(1) dict lookup, zero MCP calls.
        # refresh_if_stale() is a monotonic check and no-ops when the cache
        # is fresh; it warms lazily on the very first call per server process.
        # On first warm, registry_hints enables startup staleness logging so
        # stale entries are visible in server logs rather than per-mission.
        if self._bootstrap is not None and preferred:
            if self._bootstrap.should_refresh():
                hints = [m.catalogue_metric for m in self._metrics.all() if m.catalogue_metric]
                await self._bootstrap.warm(registry_hints=hints)
            if self._bootstrap.has(preferred):
                self._measure_cache[definition.id] = preferred
                return preferred

        # Preserve the original module-scoping contract:
        # - "seleric_module": null in YAML  → pass module=None explicitly so the
        #   gateway respects the metric's intentional unscoped access rather than
        #   inheriting the domain agent's module pin (which would restrict to the
        #   wrong catalogue subset, e.g. "paidmedia" for metric.cac).
        # - "seleric_module" key absent in YAML → let the gateway apply the
        #   agent's module pin (safest default).
        module_args: dict[str, Any] = (
            {"module": definition.seleric_module}
            if "seleric_module" in definition.raw
            else {}
        )

        # Step 1: Exact ID lookup — deterministic, not semantic.
        # Only attempted when a preferred catalogue_metric ID is known.
        # Skipped when catalogue_metric is None (no hint in registry) — go
        # straight to Step 2 semantic search.
        # catalogue_get_metric returns the full metric dict on success or
        # {"error": "Unknown metric '...'", "suggestions": [...]} on failure.
        if preferred:
            try:
                exact_result = await self._mcp.call(
                    agent_id=self._agent_id,
                    capability="seleric.catalogue_get_metric",
                    arguments={"metric_id": preferred, **module_args},
                )
            except Exception:
                self._measure_cache[definition.id] = None
                return None

            if not exact_result.get("error"):
                # Metric confirmed in catalogue — registry is current.
                self._measure_cache[definition.id] = preferred
                return preferred
            # else: preferred ID absent — fall through to Step 2

        # Step 2: No preferred ID OR preferred ID absent from catalogue (stale).
        # Search by description (business meaning, not the stale ID string) and
        # apply the keyword-overlap guard to reject wrong-domain substitutions.
        try:
            desc_result = await self._mcp.call(
                agent_id=self._agent_id,
                capability="seleric.catalogue_search_metrics",
                arguments={"query": definition.description, **module_args},
            )
        except Exception:
            self._measure_cache[definition.id] = None
            return None

        desc_matches = desc_result.get("matches") or []
        for match in desc_matches:
            candidate = match.get("id") or ""
            if candidate and _measure_keywords_overlap(definition, candidate):
                if preferred:
                    # Safe substitution — record so the operator can update the registry.
                    self._stats.record_stale_sub(definition.id, preferred, candidate)
                self._measure_cache[definition.id] = candidate
                return candidate

        # Step 3: Truly unresolvable — return None so the caller emits a clear
        # "measure not found" limitation rather than a phantom metrics_query.
        self._measure_cache[definition.id] = None
        return None

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
        directions: dict[str, str] = {}
        source_label = "seleric.metrics_query"
        missing: list[str] = []
        for metric_id in want:
            definition = self._metrics.get(metric_id)
            if definition is None:
                missing.append(metric_id)
                continue
            measure = await self._resolve_measure(definition)
            if measure is None:
                # _resolve_measure already recorded the stale-sub or searched
                # semantically and found nothing. The limitation text was set
                # there; here we just count it as a gap and skip the MCP call.
                # This distinguishes "measure not in catalogue" from "measure
                # in catalogue but no rows for this period" (below).
                self._stats.mcp_fallbacks += 1
                cat_hint = definition.catalogue_metric or "(no catalogue_metric hint)"
                self._stats.fallback_reasons.append(
                    f"{metric_id}: catalogue measure '{cat_hint}' not found "
                    "— update metric_registry.yaml or add a catalogue_metric hint"
                )
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
                # Measure exists in catalogue but has no data for this window.
                # "no data for" is intentionally different from "not found"
                # above so operators can distinguish the two failure types.
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(
                    f"{metric_id}: no data for {start}..{end}"
                )
                missing.append(metric_id)
                continue
            raw_value = rows[0].get(measure)
            if raw_value is None:
                self._stats.mcp_fallbacks += 1
                self._stats.fallback_reasons.append(f"{metric_id}: measure key absent from row")
                missing.append(metric_id)
                continue
            values[metric_id] = float(raw_value)
            units[metric_id] = definition.unit
            directions[metric_id] = definition.direction_bad
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
                direction_bad=directions.get(mid, "up"),
                dimensions={},
                data_origin="MCP",
                synthetic=False,
                source=source_label,
            )
            for mid, value in values.items()
        ]
        return DataResult(readings=readings, events=[], missing=missing, data_origin="MCP", synthetic=False)

    async def fetch_series(
        self,
        *,
        metric_ids: list[str],
        time_range: dict[str, Any],
        max_days: int = 60,
        min_rows: int = 8,
    ) -> Any:
        """Daily observation series for real causal estimation (docs/44 ROB-002).

        One ``seleric.metrics_query`` call per metric per day — the same
        catalogue/measure resolution ``fetch`` already uses, just windowed to
        single days instead of one aggregated range. Returns a pandas
        DataFrame indexed by date with one column per metric that returned
        real data, or ``None`` if the window is too wide to fetch cheaply or
        too little data came back to be useful.

        ``min_rows`` defaults to 8 — the same floor statsmodels itself warns
        below (``omni_normtest is not valid with less than 8 observations``).
        A 2-3 day anomaly window produces a rank-deficient OLS fit that DoWhy
        will still "answer" with, which is worse than the honest metadata-only
        fallback: a confident-looking number from an underdetermined model is
        exactly the fabrication the project's evidence rules exist to prevent.
        Below this floor, callers should get ``None`` and fall back.

        # ponytail: one HTTP call per (metric, day) — fine for the ~7-30 day
        # windows diagnostic missions actually use. If missions start asking
        # for multi-month windows, switch to seleric.metrics_drilldown (a
        # registered-but-unused MCP capability that can return a series in
        # one call) instead of raising max_days further.
        """
        import pandas as pd
        from datetime import date, timedelta

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

        columns: dict[str, dict[str, float]] = {}
        for metric_id in metric_ids:
            definition = self._metrics.get(metric_id)
            if definition is None:
                continue
            measure = await self._resolve_measure(definition)
            if measure is None:
                continue
            args: dict[str, Any] = {}
            if "seleric_module" in definition.raw:
                args["module"] = definition.seleric_module
            day_values: dict[str, float] = {}
            day = start
            while day <= end:
                day_s = day.isoformat()
                self._stats.mcp_attempts += 1
                try:
                    result = await self._mcp.call(
                        agent_id=self._agent_id,
                        capability="seleric.metrics_query",
                        arguments={**args, "measures": [measure], "time_range": {"start": day_s, "end": day_s}},
                    )
                except Exception:
                    day = day + timedelta(days=1)
                    continue
                rows = result.get("rows") or []
                if not result.get("error") and rows and rows[0].get(measure) is not None:
                    day_values[day_s] = float(rows[0][measure])
                day = day + timedelta(days=1)
            if len(day_values) >= min_rows:
                columns[metric_id] = day_values

        if len(columns) < 2:
            return None
        frame = pd.DataFrame(columns)
        frame = frame.dropna(how="any")
        if len(frame) < min_rows:
            return None
        return frame

    async def events(self, *, time_range: dict[str, Any]) -> list[DomainEvent]:
        del time_range
        return []


def build_hybrid_bundle(
    *,
    mcp: MCPGateway | None = None,
    execution_mode: str = "production",
    metrics: MetricRegistry,
    agents: AgentRegistry | None = None,
    bootstrap: CatalogueBootstrap | None = None,
) -> tuple[ProviderBundle, McpFetchStats]:
    """Build providers for a live mission: live MCP for domains with a
    seleric_module, no data otherwise.

    ``bootstrap`` is the shared ``CatalogueBootstrap`` instance from
    ``SwarmRuntime``.  When provided, ``_resolve_measure`` can check the
    live catalogue cache (Step 0) before making any MCP calls, eliminating
    the per-metric ``catalogue_get_metric`` round-trip on the hot path.
    """
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
                bootstrap=bootstrap,
            )
        else:
            data[d] = EmptyDataProvider(d)
    # Data is MCP/empty; intelligence seams stay Template* so specialists
    # (anomaly / lightweight diagnostic / prediction / skeptic) never see None.
    bundle = ProviderBundle(
        data=data,
        anomaly=TemplateAnomalyDetector(),
        causal=TemplateCausalEngine(),
        forecaster=TemplateForecaster(),
        optimizer=TemplateOptimizer(),
        stats=TemplateStatsEngine(),
    )
    return bundle, stats

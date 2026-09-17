"""Collect and normalize grounded business evidence."""

from __future__ import annotations

import re
from typing import Any

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.contracts.lookup import DimensionMappingV1, TimeRangeV1
from seleric_swarm.coordinator.catalogue_grounding import (
    breakdown_from_query,
    pick_grain,
    query_has_grain_intent,
    resolve_catalogue_dimension,
)
from seleric_swarm.domain.models import StateRequest
from seleric_swarm.llm.errors import LLMError, LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.evidence import make_evidence
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.domain.base import DomainAgent, DomainConfig
from seleric_swarm.swarm.providers.mcp_data import HybridMcpDataProvider, McpFetchStats

AGENT_VERSION = "0.1.0"
_TOP_N_RE = re.compile(r"\btop\s+(\d+)\b", re.IGNORECASE)
_DEFAULT_TOP_N = 10
_CURRENCY_UNITS = frozenset({"inr", "usd", "eur", "gbp", "aud", "cad", "jpy"})


def _evidence_unit(definition: Any, provenance: dict[str, Any], catalogue_unit: Any = None) -> Any:
    """Unit from catalogue_get_metric / YAML. Cube currency only for money metrics."""
    declared = catalogue_unit or getattr(definition, "unit", None)
    if declared:
        return declared
    currency = provenance.get("currency")
    if currency and str(currency).lower() in _CURRENCY_UNITS:
        return currency
    return None


def _lookup_metric_id(metrics: Any, metric_id: str, definition: Any) -> str:
    """YAML ``metric.*`` overlay id when one exists; else the assigned id.

    Live catalogue bind makes ``MetricDefinition.id`` the catalogue slug
    (``units_sold``). Lookup claim_gate / replay still key evidence on the
    overlay id (``metric.units_sold``).
    """
    overlay_for = getattr(metrics, "_overlay_for", None)
    if callable(overlay_for):
        cat = getattr(definition, "catalogue_metric", None) or metric_id
        overlay = overlay_for(metric_id) or overlay_for(cat)
        if overlay is not None:
            return overlay.id
    return str(getattr(definition, "id", None) or metric_id)


def _lookup_row(
    payload: dict[str, Any],
    *,
    definition: Any,
    metric_id: str,
    catalogue_unit: Any,
    ontology: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provenance = dict(payload.get("provenance") or {})
    if ontology:
        provenance.update({k: v for k, v in ontology.items() if v is not None})
    return make_evidence(
        source=str(payload.get("source") or ""),
        metric_or_fact=metric_id,
        value=payload.get("value"),
        unit=_evidence_unit(definition, provenance, catalogue_unit),
        dimensions=dict(payload.get("dimensions") or {}),
        time_range=dict(payload.get("time_range") or {}),
        provenance=provenance,
        freshness=payload.get("created_at"),
    )


def _comparison_deltas(evidence: list[dict[str, Any]], fallback_def: Any) -> list[dict[str, Any]]:
    """One delta row per metric that has exactly two dated observations."""
    by_metric: dict[tuple[str, tuple[tuple[str, Any], ...]], list[dict[str, Any]]] = {}
    for row in evidence:
        metric_id = str(row.get("metric_or_fact") or "")
        if metric_id.endswith(".delta"):
            continue
        dims = tuple(sorted((row.get("dimensions") or {}).items()))
        by_metric.setdefault((metric_id, dims), []).append(row)
    extras: list[dict[str, Any]] = []
    for (metric_id, _dims), rows in by_metric.items():
        if len(rows) != 2:
            continue
        left, right = rows[0], rows[1]
        extras.append(
            make_evidence(
                source="deterministic.metrics",
                metric_or_fact=f"{metric_id}.delta",
                value=float(right["value"]) - float(left["value"]),
                unit=right.get("unit") or (getattr(fallback_def, "unit", None) if fallback_def else None),
                time_range={
                    "start": (left.get("time_range") or {}).get("start"),
                    "end": (right.get("time_range") or {}).get("start"),
                },
                provenance={
                    "calculation": "right - left",
                    "left_evidence_id": left["evidence_id"],
                    "right_evidence_id": right["evidence_id"],
                    "metric_version": (right.get("provenance") or {}).get("metric_version"),
                },
            )
        )
    return extras


def _query_windows(time_range: dict[str, Any]) -> list[tuple[str, str]]:
    """Comparison = two point days. Otherwise one Cube window (start, end)."""
    kind = time_range.get("kind")
    start = time_range.get("start")
    end = time_range.get("end") or start
    if kind == "comparison" and start and end:
        return [(start, start), (end, end)]
    if start:
        return [(start, end or start)]
    return []


class Agent(SwarmAgent):
    agent_id = "observer_agent"

    def __init__(self, runtime: SwarmRuntime) -> None:
        self.runtime = runtime
        self._stats = McpFetchStats()
        self._providers: dict[str, HybridMcpDataProvider] = {}

    def _provider(self, definition: Any) -> HybridMcpDataProvider:
        domain = definition.domain
        if domain not in self._providers:
            self._providers[domain] = HybridMcpDataProvider(
                domain,
                mcp=self.runtime.mcp,
                stats=self._stats,
                metrics=self.runtime.metrics,
                agent_id=f"{domain}_agent",
                bootstrap=getattr(self.runtime, "bootstrap", None),
            )
        return self._providers[domain]

    def _domain_agent(self, definition: Any, metric_id: str) -> DomainAgent:
        domain = definition.domain
        agent_id = f"{domain}_agent"
        owned = [definition.id]
        if metric_id not in owned:
            owned.append(metric_id)
        cfg = DomainConfig(
            agent_id=agent_id,
            domain=domain,
            owned_metrics=owned,
            probe_metrics=owned,
        )
        return DomainAgent(cfg, self._provider(definition), metrics=self.runtime.metrics)

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        return await self.observe(ctx)

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        allowed = list(ctx.payload.get("allowed_metrics") or [])
        metric_ids, metric_llm_calls, map_error = await self._resolve_metric_ids(ctx, allowed)
        if map_error:
            return map_error
        if not metric_ids:
            return {
                "metric_id": None,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": "Metric is ambiguous or not in the commerce registry",
                "limitations": ["No registered metric could be selected without improvising a formula"],
                "llm_calls": metric_llm_calls,
            }

        windows = _query_windows(ctx.payload.get("time_range") or {})
        if not windows:
            return {
                "metric_id": metric_ids[0],
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": "No resolved date for lookup",
                "limitations": ["Time range could not be resolved"],
                "llm_calls": metric_llm_calls,
            }

        seleric_live = bool(
            {"seleric.metrics_query", "seleric.catalogue_get_metric", "seleric.catalogue_search_metrics"}
            & self.runtime.mcp.capabilities
        )
        if not seleric_live:
            return {
                "metric_id": metric_ids[0],
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": "Live Seleric MCP is not configured",
                "limitations": ["SELERIC_MCP_URL/TOKEN required for metric retrieval"],
                "llm_calls": metric_llm_calls,
                "mcp_called": False,
            }

        evidence: list[dict[str, Any]] = []
        missing: list[str] = []
        tool_calls = 0
        last_definition = None
        requested_dimensions: list[str] = []
        grain_unsupported = False
        for metric_id in metric_ids:
            definition = self.runtime.metrics.get(metric_id)
            if definition is None:
                missing.append(metric_id)
                continue
            last_definition = definition
            owner_agent_id = f"{definition.domain}_agent"
            evidence_metric = _lookup_metric_id(self.runtime.metrics, metric_id, definition)
            seleric_measure, supported, catalogue_unit = await self._resolve_seleric_measure(
                definition=definition, owner_agent_id=owner_agent_id
            )
            dim_ids: list[str] = []
            if seleric_measure:
                dim_ids = await self._resolve_breakdown_dimensions(ctx, supported=supported)
                wanted = [d for d in (ctx.payload.get("resolved_dimensions") or []) if d]
                if query_has_grain_intent(ctx.question or "") and wanted and not dim_ids:
                    grain_unsupported = True
                    continue
            limit = None
            sort = None
            if dim_ids and seleric_measure:
                found = _TOP_N_RE.search(ctx.question or "")
                limit = max(1, min(int(found.group(1)), 25)) if found else _DEFAULT_TOP_N
                sort = [{"field": seleric_measure, "direction": "desc"}]
            if seleric_measure:
                self._provider(definition)._measure_cache[definition.id] = seleric_measure
            om: dict[str, Any] = {}
            ontology = getattr(self.runtime, "ontology", None)
            if ontology is not None and seleric_measure:
                om = await ontology.metric_context(seleric_measure, agent_id=owner_agent_id)
            domain_agent = self._domain_agent(definition, metric_id)
            for start, end in windows:
                tool_calls += 1
                if not seleric_measure:
                    missing.append(f"{metric_id} on {start}" + (f"–{end}" if end != start else ""))
                    continue
                board = Blackboard(ctx.mission_id)
                posted = await domain_agent.observe(
                    board,
                    time_range={"start": start, "end": end},
                    extra_metrics=[metric_id],
                    grain=dim_ids or None,
                    limit=limit,
                    sort=sort,
                )
                rows = [
                    _lookup_row(
                        board.get(aid) or {},
                        definition=definition,
                        metric_id=evidence_metric,
                        catalogue_unit=catalogue_unit,
                        ontology=om,
                    )
                    for aid in posted
                    if board.get(aid)
                ]
                if not rows:
                    missing.append(f"{metric_id} on {start}" + (f"–{end}" if end != start else ""))
                    continue
                for row in rows:
                    for dim in (row.get("dimensions") or {}):
                        if dim not in requested_dimensions:
                            requested_dimensions.append(dim)
                evidence.extend(rows)

        if grain_unsupported and not evidence:
            return {
                "metric_id": metric_ids[0],
                "evidence": [],
                "mcp_called": True,
                "tool_calls": tool_calls,
                "llm_calls": metric_llm_calls,
                "requested_dimensions": [],
                "error_code": "GRAIN_UNSUPPORTED",
                "error_message": "Asked grain is not supported by the assigned metric",
                "limitations": ["The assigned metric cannot slice by the requested dimension"],
            }

        if missing and not evidence:
            return {
                "metric_id": metric_ids[0],
                "evidence": [],
                "mcp_called": True,
                "tool_calls": tool_calls,
                "llm_calls": metric_llm_calls,
                "requested_dimensions": requested_dimensions,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": f"No data for {', '.join(missing)}",
                "limitations": [f"No evidence for {item}" for item in missing],
            }

        if ctx.payload.get("query_class") == "comparison":
            evidence.extend(_comparison_deltas(evidence, last_definition))

        primary = metric_ids[0]
        limitations = [f"No evidence for {item}" for item in missing]
        if grain_unsupported:
            limitations.append("The assigned metric cannot slice by the requested dimension")
        status = "partial" if (missing or grain_unsupported) and evidence else ("failed" if missing else None)
        return {
            "metric_id": primary,
            "evidence": evidence,
            "evidence_refs": [row["evidence_id"] for row in evidence],
            "mcp_called": True,
            "tool_calls": tool_calls,
            "llm_calls": metric_llm_calls,
            "requested_dimensions": requested_dimensions,
            "limitations": limitations,
            "error_code": "INSUFFICIENT_EVIDENCE" if missing else None,
            "status": status,
        }

    async def business_state_evidence(
        self, ctx: AgentContext, *, metric_id: str, time_range: TimeRangeV1
    ) -> list[dict[str, Any]]:
        """Sprint 1 proof of the BusinessStateService caller contract
        (docs/features/business-state-service/03_DEFINITIONS_TO_MAKE_FUNCTIONAL.md#7-caller-matrix-wiring-contract).

        Additive only -- not called from ``observe()``'s production path yet.
        Replacing ``_comparison_deltas`` with these features is a later,
        separate change once the facade has broader metric coverage.
        """
        business_state = getattr(self.runtime, "business_state", None)
        if business_state is None:
            return []
        definition = self.runtime.metrics.get(metric_id)
        if definition is None:
            return []
        request = StateRequest(
            metric_id=metric_id,
            time_range=time_range,
            dimensions={"brand_id": str(ctx.payload.get("brand_id") or "20")},
            agent_id=f"{definition.domain}_agent",
            need=["actual", "features"],
        )
        state = await business_state.get_metric_state(request)
        if state.status == "UNAVAILABLE":
            return []
        rows = [
            make_evidence(
                source="deterministic.business_state",
                metric_or_fact=metric_id,
                value=state.actual,
                unit=definition.unit,
                dimensions=state.dimensions,
                time_range=state.window,
                freshness=state.freshness,
                provenance=state.provenance,
                quality_flags=state.quality_flags,
            )
        ]
        for feature_id, feature in state.features.items():
            rows.append(
                make_evidence(
                    source="deterministic.business_state",
                    metric_or_fact=f"{metric_id}.feature.{feature_id}",
                    value=feature.value,
                    unit=definition.unit,
                    dimensions=state.dimensions,
                    time_range=state.window,
                    freshness=state.freshness,
                    provenance=state.provenance,
                    quality_flags=state.quality_flags,
                )
            )
        return rows

    async def _resolve_metric_ids(
        self, ctx: AgentContext, allowed: list[str]
    ) -> tuple[list[str], int, dict[str, Any] | None]:
        allowed_set = set(allowed)
        hints = [h for h in (ctx.payload.get("metric_hints") or []) if h in allowed_set]
        if hints:
            primary = ctx.payload.get("metric_id")
            question = (ctx.question or "").lower()
            if (
                query_has_grain_intent(ctx.question or "")
                and primary in allowed_set
                and " and " not in question
            ):
                return [primary], 0, None
            return hints, 0, None
        preset = ctx.payload.get("metric_id")
        if preset and preset in allowed_set:
            return [preset], 0, None
        if ctx.payload.get("resolved_dimensions") and query_has_grain_intent(ctx.question or ""):
            return [], 0, {
                "metric_id": None,
                "error_code": "GRAIN_UNSUPPORTED",
                "error_message": "Asked grain is not supported by the assigned metric",
                "limitations": ["No assigned metric supports the requested dimension"],
                "llm_calls": 0,
            }
        return [], 0, {
            "metric_id": None,
            "error_code": "INSUFFICIENT_EVIDENCE",
            "error_message": "No assigned metric for this domain retrieve",
            "limitations": ["Observer will not map the raw query against the domain dump"],
            "llm_calls": 0,
        }

    async def _resolve_seleric_measure(
        self, *, definition: Any, owner_agent_id: str
    ) -> tuple[str | None, list[str], Any]:
        """Exact catalogue identity + live supported dims for lookup grain.

        Bootstrap can confirm the measure id, but grain still needs
        ``catalogue_get_metric.supported_dimensions`` — the bootstrap cache
        is often identity-only.
        """
        preferred = getattr(definition, "catalogue_metric", None) or definition.id.removeprefix("metric.")
        caps = self.runtime.mcp.capabilities
        bootstrap = getattr(self.runtime, "bootstrap", None)
        measure: str | None = None
        supported: list[str] = []
        unit: Any = None
        if bootstrap is not None and preferred and getattr(bootstrap, "has", None) and bootstrap.has(preferred):
            meta = bootstrap.get(preferred)
            measure = preferred
            supported = list(getattr(meta, "supported_dimensions", None) or [])
            unit = (getattr(meta, "raw", None) or {}).get("unit")
        if preferred and "seleric.catalogue_get_metric" in caps:
            args: dict[str, Any] = {"metric_id": preferred}
            if "seleric_module" in getattr(definition, "raw", {}):
                args["module"] = definition.seleric_module
            try:
                payload = await self.runtime.mcp.call(
                    agent_id=owner_agent_id,
                    capability="seleric.catalogue_get_metric",
                    arguments=args,
                )
            except Exception:
                payload = {}
            if payload and not payload.get("error"):
                return (
                    preferred,
                    list(payload.get("supported_dimensions") or supported),
                    payload.get("unit") if payload.get("unit") is not None else unit,
                )
            if measure:
                return measure, supported, unit
            if not getattr(definition, "catalogue_metric", None):
                return None, [], None
            return preferred, supported, unit
        if measure:
            return measure, supported, unit
        if getattr(definition, "catalogue_metric", None):
            return preferred, supported, unit
        if "seleric.catalogue_search_metrics" not in caps:
            return None, [], None
        args = {"query": preferred}
        if "seleric_module" in getattr(definition, "raw", {}):
            args["module"] = definition.seleric_module
        result = await self.runtime.mcp.call(
            agent_id=owner_agent_id,
            capability="seleric.catalogue_search_metrics",
            arguments=args,
        )
        for match in result.get("matches") or []:
            if match.get("id") == preferred:
                return preferred, supported, unit
        return None, [], None

    async def _resolve_breakdown_dimensions(
        self, ctx: AgentContext, *, supported: list[str]
    ) -> list[str]:
        """Pick a breakdown dim from catalogue ∩ this metric's supported list.

        Ambiguous catalogue candidates are resolved by intersection with
        ``supported`` (the metric's real dimensions), then ontology
        ``grain_defaults``. The LLM dimension_map prompt is only the last
        tie-break when the catalogue did not settle.
        """
        if not supported:
            return []
        question = ctx.question or ""
        if not query_has_grain_intent(question):
            return []
        deterministic = breakdown_from_query(question, supported)
        if deterministic:
            return deterministic[:1]
        preset = [
            d
            for d in (ctx.payload.get("resolved_dimensions") or [])
            if d in supported
        ]
        if preset:
            return preset[:1]
        live = await resolve_catalogue_dimension(ctx.question or "", runtime=self.runtime)
        for entity in ctx.payload.get("entities") or []:
            more = await resolve_catalogue_dimension(str(entity), runtime=self.runtime)
            for dim in more:
                if dim not in live:
                    live.append(dim)
        grounded = [d for d in live if d in supported]
        if len(grounded) == 1:
            return grounded
        if len(grounded) > 1:
            defaults = {}
            bootstrap = getattr(self.runtime, "bootstrap", None)
            if bootstrap is not None and getattr(bootstrap, "grain_defaults", None):
                defaults = bootstrap.grain_defaults()
            picked = pick_grain(
                grounded,
                query=ctx.question or "",
                defaults=defaults,
                registry_supported=set(supported),
            )
            if picked:
                return [picked]
        if getattr(self.runtime, "llm", None) is None:
            return []
        spec = self.runtime.prompts.load("observer.dimension_map")
        user = spec.render_user({"query": ctx.question, "supported_dimensions": ", ".join(supported)})
        request = LLMRequest(
            messages=[
                ChatMessage(role="system", content=spec.system),
                ChatMessage(role="user", content=user),
            ],
            model=spec.model,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
            timeout_s=self.runtime.settings.llm_timeout_s,
            metadata=LLMRequestMetadata(
                request_id=str(ctx.payload.get("request_id") or ctx.mission_id),
                session_id=str(ctx.payload.get("session_id") or ctx.mission_id),
                mission_id=ctx.mission_id,
                task_id=ctx.task_id,
                agent_id=self.agent_id,
                agent_version=self.runtime.agents.version(self.agent_id, AGENT_VERSION),
                prompt_id=spec.id,
                prompt_version=spec.version,
                workflow_name=self.runtime.settings.workflow_name,
                workflow_version=self.runtime.settings.workflow_version,
                model=spec.model,
                query_class=str(ctx.payload.get("query_class") or "") or None,
            ),
            tags=["observer", "dimension_map"],
        )
        try:
            mapped = await self.runtime.llm.complete_structured(request, DimensionMappingV1)
        except (LLMError, LLMStructuredOutputError):
            return []
        return [d for d in mapped.value.dimensions if d in supported][:1]

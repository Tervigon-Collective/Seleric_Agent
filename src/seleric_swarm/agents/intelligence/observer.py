"""Collect and normalize grounded business evidence."""

from __future__ import annotations

import re
from typing import Any

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.contracts.lookup import MetricMappingV1
from seleric_swarm.llm.errors import LLMError, LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.evidence import make_evidence

AGENT_VERSION = "0.1.0"
_TOP_N_RE = re.compile(r"\btop\s+(\d+)\b", re.IGNORECASE)
_DEFAULT_TOP_N = 10


def _resolve_dimension(entity: str, supported: list[str]) -> str | None:
    """Map a control-plane entity onto a catalogue dimension id."""
    entity = (entity or "").strip()
    if not entity:
        return None
    if not supported or entity in supported:
        return entity
    token = entity.removeprefix("lt_")
    matches = [
        dim
        for dim in supported
        if dim == entity or dim.removeprefix("lt_") == token or token in dim.split("_")
    ]
    return matches[0] if matches else None


def _rank_breakdown(
    question: str,
    *,
    entities: list[str],
    supported_dimensions: list[str] | None = None,
) -> dict[str, Any] | None:
    """Break down when the control plane named a catalogue dimension in entities."""
    n = _DEFAULT_TOP_N
    found = _TOP_N_RE.search(question or "")
    if found:
        n = max(1, min(int(found.group(1)), 25))
    supported = list(supported_dimensions or [])
    dims: list[str] = []
    for entity in entities:
        resolved = _resolve_dimension(entity, supported)
        if resolved:
            dims.append(resolved)
    dims = list(dict.fromkeys(dims))
    if not dims:
        return None
    return {"dimensions": dims[:1], "limit": n}


def _dimension_value(row: dict[str, Any], dim_id: str) -> Any:
    if dim_id in row:
        return row[dim_id]
    suffix = f".{dim_id}"
    for key, value in row.items():
        if str(key).endswith(suffix):
            return value
    return None


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

        seleric_live = "seleric.catalogue_search_metrics" in self.runtime.mcp.capabilities
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
        for metric_id in metric_ids:
            definition = self.runtime.metrics.get(metric_id)
            if definition is None:
                missing.append(metric_id)
                continue
            last_definition = definition
            owner_agent_id = f"{definition.domain}_agent"
            seleric_measure = await self._resolve_seleric_measure(
                definition=definition, owner_agent_id=owner_agent_id
            )
            for start, end in windows:
                tool_calls += 1
                rows = []
                if seleric_measure:
                    rows = await self._fetch_seleric(
                        seleric_measure=seleric_measure,
                        metric_id=metric_id,
                        definition=definition,
                        start=start,
                        end=end,
                        owner_agent_id=owner_agent_id,
                        question=ctx.question,
                        entities=list(ctx.payload.get("entities") or []),
                    )
                if not rows:
                    missing.append(f"{metric_id} on {start}" + (f"–{end}" if end != start else ""))
                    continue
                evidence.extend(rows)

        if missing and not evidence:
            return {
                "metric_id": metric_ids[0],
                "evidence": [],
                "mcp_called": True,
                "tool_calls": tool_calls,
                "llm_calls": metric_llm_calls,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": f"No data for {', '.join(missing)}",
                "limitations": [f"No evidence for {item}" for item in missing],
            }

        if ctx.payload.get("query_class") == "comparison":
            evidence.extend(_comparison_deltas(evidence, last_definition))

        primary = metric_ids[0]
        limitations = [f"No evidence for {item}" for item in missing]
        status = "partial" if missing and evidence else ("failed" if missing else None)
        return {
            "metric_id": primary,
            "evidence": evidence,
            "evidence_refs": [row["evidence_id"] for row in evidence],
            "mcp_called": True,
            "tool_calls": tool_calls,
            "llm_calls": metric_llm_calls,
            "limitations": limitations,
            "error_code": "INSUFFICIENT_EVIDENCE" if missing else None,
            "status": status,
        }

    async def _resolve_metric_ids(
        self, ctx: AgentContext, allowed: list[str]
    ) -> tuple[list[str], int, dict[str, Any] | None]:
        hints = [h for h in (ctx.payload.get("metric_hints") or []) if h in allowed]
        if len(hints) > 1:
            return hints, 0, None
        preset = ctx.payload.get("metric_id")
        if preset and preset in allowed:
            return [preset], 0, None
        if len(hints) == 1:
            return hints, 0, None

        spec = self.runtime.prompts.load("observer.metric_map")
        user = spec.render_user(
            {
                "query": ctx.question,
                "allowed_metric_ids": ", ".join(allowed),
                "metric_hints": ", ".join(ctx.payload.get("metric_hints") or []),
            }
        )
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
            tags=["observer", "metric_map"],
        )
        try:
            mapped = await self.runtime.llm.complete_structured(request, MetricMappingV1)
        except (LLMError, LLMStructuredOutputError) as exc:
            message = getattr(exc, "message", str(exc))
            return [], 1, {
                "error_code": "LLM_UNAVAILABLE",
                "error_message": message,
                "limitations": ["Observer could not map a canonical metric"],
                "llm_calls": 1,
            }
        mapping = mapped.value
        metric_id = mapping.metric_id
        if mapping.ambiguous or not metric_id or metric_id not in allowed:
            return [], 1, {
                "metric_id": metric_id,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": mapping.reason or "Metric is ambiguous or not in the commerce registry",
                "limitations": ["No registered metric could be selected without improvising a formula"],
                "llm_calls": 1,
            }
        if self.runtime.metrics.get(metric_id) is None:
            return [], 1, {
                "metric_id": metric_id,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": f"{metric_id} is not in the metric registry",
                "limitations": ["Unknown metric id; observer will not invent a formula"],
                "llm_calls": 1,
            }
        return [metric_id], 1, None

    async def _resolve_seleric_measure(self, *, definition: Any, owner_agent_id: str) -> str | None:
        """Resolve the catalogue measure id for this registry metric."""

        preferred = getattr(definition, "catalogue_metric", None) or definition.id.removeprefix("metric.")
        args: dict[str, Any] = {"query": preferred}
        if "seleric_module" in getattr(definition, "raw", {}):
            args["module"] = definition.seleric_module
        result = await self.runtime.mcp.call(
            agent_id=owner_agent_id,
            capability="seleric.catalogue_search_metrics",
            arguments=args,
        )
        matches = result.get("matches") or []
        for match in matches:
            if match.get("id") == preferred:
                return preferred
        if matches:
            return matches[0].get("id")
        # Registry already named a catalogue id — try it even if search ranked nothing.
        if getattr(definition, "catalogue_metric", None):
            return preferred
        return None

    async def _fetch_seleric(
        self,
        *,
        seleric_measure: str,
        metric_id: str,
        definition: Any,
        start: str,
        end: str,
        owner_agent_id: str,
        question: str = "",
        entities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        arguments: dict[str, Any] = {
            "measures": [seleric_measure],
            "time_range": {"start": start, "end": end},
        }
        supported: list[str] = []
        if entities and "seleric.catalogue_get_metric" in self.runtime.mcp.capabilities:
            try:
                payload = await self.runtime.mcp.call(
                    agent_id=owner_agent_id,
                    capability="seleric.catalogue_get_metric",
                    arguments={"metric_id": seleric_measure},
                )
                supported = list(payload.get("supported_dimensions") or [])
            except Exception:
                supported = []
        rank = _rank_breakdown(
            question,
            entities=list(entities or []),
            supported_dimensions=supported,
        )
        if rank:
            arguments["dimensions"] = list(rank["dimensions"])
            arguments["sort"] = [{"field": seleric_measure, "direction": "desc"}]
            arguments["limit"] = rank["limit"]
        if "seleric_module" in getattr(definition, "raw", {}):
            arguments["module"] = definition.seleric_module
        result = await self.runtime.mcp.call(
            agent_id=owner_agent_id,
            capability="seleric.metrics_query",
            arguments=arguments,
        )
        # The catalogue module-scope refusal comes back as a normal payload
        # (an "error" key), not a raised exception -- treat it as missing data.
        rows = result.get("rows") or []
        if result.get("error") or not rows:
            return []
        provenance = result.get("provenance") or {}
        om: dict[str, Any] = {}
        ontology = getattr(self.runtime, "ontology", None)
        if ontology is not None:
            om = await ontology.metric_context(seleric_measure, agent_id=owner_agent_id)
        dim_ids = list(rank["dimensions"]) if rank else []
        out: list[dict[str, Any]] = []
        for row in rows:
            raw_value = row.get(seleric_measure)
            if raw_value is None:
                continue
            dims = {d: _dimension_value(row, d) for d in dim_ids}
            dims = {k: v for k, v in dims.items() if v is not None}
            out.append(
                make_evidence(
                    source=f"seleric_mcp.{provenance.get('cube_view', seleric_measure)}",
                    metric_or_fact=metric_id,
                    value=float(raw_value),
                    unit=definition.unit or provenance.get("currency"),
                    dimensions=dims,
                    time_range={"start": start, "end": end, "timezone": provenance.get("timezone")},
                    freshness=provenance.get("generated_at"),
                    provenance={
                        "server": "seleric_mcp",
                        "tool_name": "seleric.metrics_query",
                        "resolved_measure": seleric_measure,
                        "query_id": provenance.get("query_id"),
                        "cube_view": provenance.get("cube_view"),
                        "catalogue_version": provenance.get("catalogue_version"),
                        "freshness": provenance.get("freshness"),
                        "requested_time_range": {"start": start, "end": end},
                        "metric_version": definition.version,
                        "formula": definition.formula,
                        "data_product": om.get("data_product"),
                        "contract": om.get("contract"),
                        "entity_cluster": om.get("entity_cluster"),
                        "om_domain": om.get("domain"),
                    },
                )
            )
        return out

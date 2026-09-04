"""Collect and normalize grounded business evidence."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.contracts.lookup import MetricMappingV1
from seleric_swarm.llm.errors import LLMError, LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.protocols.mcp.gateway import FIXTURE_CAPABILITY_BY_DOMAIN
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.evidence import make_evidence

AGENT_VERSION = "0.1.0"


def _dates_from_time_range(time_range: dict[str, Any]) -> list[str]:
    kind = time_range.get("kind")
    start = time_range.get("start")
    end = time_range.get("end")
    if kind == "comparison" and start and end:
        return [start, end]
    if start:
        return [start]
    return []


class Agent(SwarmAgent):
    agent_id = "observer_agent"

    def __init__(self, runtime: SwarmRuntime) -> None:
        self.runtime = runtime

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        return await self.observe(ctx)

    async def observe(self, ctx: AgentContext) -> dict[str, Any]:
        allowed = list(ctx.payload.get("allowed_metrics") or [])

        # Deterministic-before-LLM: if the coordinator already resolved a single
        # canonical registry id, skip the metric-mapping model call entirely.
        preset = ctx.payload.get("metric_id")
        if preset and preset in allowed:
            mapping = MetricMappingV1(metric_id=preset, ambiguous=False)
            metric_llm_calls = 0
        else:
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
                mapping = mapped.value
            except (LLMError, LLMStructuredOutputError) as exc:
                message = getattr(exc, "message", str(exc))
                return {
                    "error_code": "LLM_UNAVAILABLE",
                    "error_message": message,
                    "limitations": ["Observer could not map a canonical metric"],
                    "llm_calls": 1,
                }
            metric_llm_calls = 1

        metric_id = mapping.metric_id
        if mapping.ambiguous or not metric_id or metric_id not in allowed:
            return {
                "metric_id": metric_id,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": mapping.reason or "Metric is ambiguous or not in the commerce registry",
                "limitations": ["No registered metric could be selected without improvising a formula"],
                "llm_calls": metric_llm_calls,
            }

        definition = self.runtime.metrics.get(metric_id)
        if definition is None:
            return {
                "metric_id": metric_id,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": f"{metric_id} is not in the metric registry",
                "limitations": ["Unknown metric id; observer will not invent a formula"],
                "llm_calls": metric_llm_calls,
            }

        dates = _dates_from_time_range(ctx.payload.get("time_range") or {})
        if not dates:
            return {
                "metric_id": metric_id,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": "No resolved date for lookup",
                "limitations": ["Time range could not be resolved"],
                "llm_calls": metric_llm_calls,
            }

        owner_agent_id = f"{definition.domain}_agent"
        # No local table of "which MCP tool serves this metric": if the live
        # seleric catalogue is configured in this process, ask it what canonical
        # measure answers the metric's own description (scoped to the owning
        # domain's module, which the gateway pins automatically). Otherwise fall
        # back to the domain's local fixture server, if it has one.
        seleric_live = "seleric.catalogue_search_metrics" in self.runtime.mcp.capabilities
        seleric_measure = None
        if seleric_live:
            seleric_measure = await self._resolve_seleric_measure(
                definition=definition, owner_agent_id=owner_agent_id
            )
        fixture_capability = FIXTURE_CAPABILITY_BY_DOMAIN.get(definition.domain)

        evidence: list[dict[str, Any]] = []
        missing: list[str] = []
        tool_calls = 0
        for day in dates:
            tool_calls += 1
            row_evidence = None
            if seleric_measure:
                row_evidence = await self._fetch_seleric(
                    seleric_measure=seleric_measure,
                    metric_id=metric_id,
                    definition=definition,
                    day=day,
                    owner_agent_id=owner_agent_id,
                )
            elif not seleric_live and fixture_capability:
                row_evidence = await self._fetch_fixture(
                    capability=fixture_capability, metric_id=metric_id, definition=definition, day=day
                )
            if row_evidence is None:
                missing.append(day)
                continue
            evidence.append(row_evidence)

        if missing and not evidence:
            return {
                "metric_id": metric_id,
                "evidence": [],
                "mcp_called": True,
                "tool_calls": tool_calls,
                "llm_calls": metric_llm_calls,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": f"No data for {', '.join(missing)}",
                "limitations": [
                    f"No evidence for {metric_id} on {day}" for day in missing
                ],
            }

        if ctx.payload.get("query_class") == "comparison" and len(evidence) == 2:
            left = evidence[0]["value"]
            right = evidence[1]["value"]
            delta = float(right) - float(left)
            evidence.append(
                make_evidence(
                    source="deterministic.metrics",
                    metric_or_fact=f"{metric_id}.delta",
                    value=delta,
                    unit=definition.unit,
                    time_range={
                        "start": evidence[0]["time_range"].get("start"),
                        "end": evidence[1]["time_range"].get("start"),
                    },
                    provenance={
                        "calculation": "right - left",
                        "left_evidence_id": evidence[0]["evidence_id"],
                        "right_evidence_id": evidence[1]["evidence_id"],
                        "metric_version": definition.version,
                    },
                )
            )

        limitations = [f"No evidence for {metric_id} on {day}" for day in missing]
        status = "partial" if missing and evidence else ("failed" if missing else None)
        return {
            "metric_id": metric_id,
            "evidence": evidence,
            "evidence_refs": [row["evidence_id"] for row in evidence],
            "mcp_called": True,
            "tool_calls": tool_calls,
            "llm_calls": metric_llm_calls,
            "limitations": limitations,
            "error_code": "INSUFFICIENT_EVIDENCE" if missing else None,
            "status": status,
        }

    async def _fetch_fixture(
        self, *, capability: str, metric_id: str, definition: Any, day: str
    ) -> dict[str, Any] | None:
        result = await self.runtime.mcp.call(
            agent_id="observer_agent",
            capability=capability,
            arguments={"date": day, "metrics": [metric_id]},
        )
        # Tool-returned text is untrusted and must not affect numeric values.
        _ = result.get("raw_untrusted_text")
        if not result.get("found") or metric_id not in (result.get("metrics") or {}):
            return None
        value = result["metrics"][metric_id]
        server = "performance_fixture" if capability.startswith("performance") else "commerce_fixture"
        return make_evidence(
            source=result.get("source", f"fixture.{capability}"),
            metric_or_fact=metric_id,
            value=value,
            unit=definition.unit or result.get("currency"),
            time_range={"start": day, "end": day, "timezone": result.get("timezone")},
            freshness=result.get("retrieved_at"),
            provenance={
                "server": server,
                "tool_name": capability,
                "tool_version": result.get("tool_version"),
                "query_hash": result.get("query_hash"),
                "requested_time_range": {"start": day, "end": day},
                "timezone": result.get("timezone"),
                "row_count": result.get("row_count"),
                "metric_version": definition.version,
                "formula": definition.formula,
                "retrieval_timestamp": result.get("retrieved_at"),
            },
        )

    async def _resolve_seleric_measure(self, *, definition: Any, owner_agent_id: str) -> str | None:
        """Ask the live catalogue which measure answers this metric -- no local
        metric -> MCP-tool table to keep in sync by hand."""

        result = await self.runtime.mcp.call(
            agent_id=owner_agent_id,
            capability="seleric.catalogue_search_metrics",
            arguments={"query": definition.description or definition.id},
        )
        matches = result.get("matches") or []
        if not matches:
            return None
        # Text relevance can rank a same-named-but-wrong-platform measure above
        # the canonical one (e.g. "amazon_gross_revenue" outscoring "gross_sales"
        # for a Shopify metric); prefer an exact id match to our own metric name
        # when the catalogue returned one, before trusting the ranking.
        bare_id = definition.id.removeprefix("metric.")
        for match in matches:
            if match.get("id") == bare_id:
                return bare_id
        return matches[0].get("id")

    async def _fetch_seleric(
        self,
        *,
        seleric_measure: str,
        metric_id: str,
        definition: Any,
        day: str,
        owner_agent_id: str,
    ) -> dict[str, Any] | None:
        result = await self.runtime.mcp.call(
            agent_id=owner_agent_id,
            capability="seleric.metrics_query",
            arguments={"measures": [seleric_measure], "time_range": {"start": day, "end": day}},
        )
        # The catalogue module-scope refusal comes back as a normal payload
        # (an "error" key), not a raised exception -- treat it as missing data.
        rows = result.get("rows") or []
        if result.get("error") or not rows:
            return None
        raw_value = rows[0].get(seleric_measure)
        if raw_value is None:
            return None
        provenance = result.get("provenance") or {}
        return make_evidence(
            source=f"seleric_mcp.{provenance.get('cube_view', seleric_measure)}",
            metric_or_fact=metric_id,
            value=float(raw_value),
            unit=definition.unit or provenance.get("currency"),
            time_range={"start": day, "end": day, "timezone": provenance.get("timezone")},
            freshness=provenance.get("generated_at"),
            provenance={
                "server": "seleric_mcp",
                "tool_name": "seleric.metrics_query",
                "resolved_measure": seleric_measure,
                "query_id": provenance.get("query_id"),
                "cube_view": provenance.get("cube_view"),
                "catalogue_version": provenance.get("catalogue_version"),
                "freshness": provenance.get("freshness"),
                "requested_time_range": {"start": day, "end": day},
                "metric_version": definition.version,
                "formula": definition.formula,
            },
        )

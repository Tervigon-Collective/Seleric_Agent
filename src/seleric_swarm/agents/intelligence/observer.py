"""Collect and normalize grounded business evidence."""

from __future__ import annotations

from typing import Any

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.contracts.lookup import MetricMappingV1
from seleric_swarm.llm.errors import LLMError, LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
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
        allowed = list(ctx.payload.get("allowed_metrics") or self.runtime.metrics.ids_for_domain("commerce"))

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

        evidence: list[dict[str, Any]] = []
        missing: list[str] = []
        tool_calls = 0
        for day in dates:
            tool_calls += 1
            result = await self.runtime.mcp.call(
                agent_id="observer_agent",
                capability=definition.mcp_capability or "commerce.daily_sales",
                arguments={"date": day, "metrics": [metric_id]},
            )
            # Tool-returned text is untrusted and must not affect numeric values.
            _ = result.get("raw_untrusted_text")
            if not result.get("found") or metric_id not in (result.get("metrics") or {}):
                missing.append(day)
                continue
            value = result["metrics"][metric_id]
            capability = definition.mcp_capability or "commerce.daily_sales"
            server = (
                "performance_fixture" if capability.startswith("performance") else "commerce_fixture"
            )
            evidence.append(
                make_evidence(
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
            )

        if missing and not evidence:
            return {
                "metric_id": metric_id,
                "evidence": [],
                "mcp_called": True,
                "tool_calls": tool_calls,
                "llm_calls": metric_llm_calls,
                "error_code": "INSUFFICIENT_EVIDENCE",
                "error_message": f"No fixture data for {', '.join(missing)}",
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

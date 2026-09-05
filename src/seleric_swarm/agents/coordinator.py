"""Plan missions, route capabilities, and emit structured classification."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.contracts.lookup import CoordinatorClassificationV1
from seleric_swarm.coordinator.catalogue_grounding import (
    entities_from_catalogue,
    hints_from_catalogue,
)
from seleric_swarm.coordinator.planning.complexity import looks_like_diagnostic
from seleric_swarm.llm.errors import LLMError, LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.metrics import lead_agent_for_hints
from seleric_swarm.services.time_range import resolve_time_range, window_from_query

AGENT_VERSION = "0.1.0"


class Agent(SwarmAgent):
    agent_id = "coordinator_agent"

    def __init__(self, runtime: SwarmRuntime) -> None:
        self.runtime = runtime

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        return await self.classify(
            query=ctx.question,
            timezone=str(ctx.payload.get("timezone") or "Asia/Kolkata"),
            as_of=ctx.payload.get("as_of"),
            mission_id=ctx.mission_id,
            request_id=str(ctx.payload.get("request_id") or ctx.mission_id),
            session_id=str(ctx.payload.get("session_id") or ctx.mission_id),
            task_id=ctx.task_id,
        )

    async def classify(
        self,
        *,
        query: str,
        timezone: str,
        as_of: str | None,
        mission_id: str,
        request_id: str,
        session_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        spec = self.runtime.prompts.load("coordinator.classify")
        user = spec.render_user(
            {
                "query": query,
                "timezone": timezone,
                "as_of": as_of or "none",
                "registry_catalog": self.runtime.metrics.catalog_prompt(),
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
                request_id=request_id,
                session_id=session_id,
                mission_id=mission_id,
                task_id=task_id,
                agent_id=self.agent_id,
                agent_version=self.runtime.agents.version(self.agent_id, AGENT_VERSION),
                prompt_id=spec.id,
                prompt_version=spec.version,
                workflow_name=self.runtime.settings.workflow_name,
                workflow_version=self.runtime.settings.workflow_version,
                model=spec.model,
            ),
            tags=["coordinator", "classify", spec.id],
        )
        try:
            result = await self.runtime.llm.complete_structured(request, CoordinatorClassificationV1)
        except LLMStructuredOutputError as exc:
            return {
                "query_class": "unsupported",
                "mission_lead": "coordinator_agent",
                "error_code": "LLM_UNAVAILABLE",
                "error_message": f"Coordinator structured output failed: {exc.message}",
                "unsupported_reason": "Coordinator could not produce a valid classification",
                "llm_calls": 1,
            }
        except LLMError as exc:
            return {
                "query_class": "unsupported",
                "mission_lead": "coordinator_agent",
                "error_code": "LLM_UNAVAILABLE",
                "error_message": exc.message,
                "unsupported_reason": "LLM unavailable during classification",
                "llm_calls": 1,
            }

        classification: CoordinatorClassificationV1 = result.value
        window = window_from_query(query, timezone, as_of)
        try:
            resolved = window or resolve_time_range(classification.time_range, timezone, as_of)
        except ValueError as exc:
            return {
                "query_class": "unsupported",
                "mission_lead": classification.domain_lead,
                "unsupported_reason": str(exc),
                "error_code": "INVALID_REQUEST",
                "error_message": str(exc),
                "llm_calls": 1,
            }

        # Skip observer's LLM metric-mapping call when a single registered
        # metric is already named. Union catalogue search (the live glossary)
        # with the classifier — never a local phrase table.
        catalogue_hints = []
        if not looks_like_diagnostic(query):
            catalogue_hints = await hints_from_catalogue(query, runtime=self.runtime, agent_id=self.agent_id)
        merged_hints = list(dict.fromkeys([*classification.metric_hints, *catalogue_hints]))
        canonical = [m for m in merged_hints if self.runtime.metrics.get(m) is not None]
        preset_metric = canonical[0] if len(canonical) == 1 else None

        query_class = classification.query_class
        domain_lead = classification.domain_lead
        unsupported_reason = classification.unsupported_reason
        reason_l = (unsupported_reason or "").lower()
        policy_block = any(token in reason_l for token in ("v1 only", "policy", "injection", "system prompt"))
        if (
            query_class == "unsupported"
            and canonical
            and not looks_like_diagnostic(query)
            and not policy_block
        ):
            query_class = "lookup"
            domain_lead = lead_agent_for_hints(canonical, self.runtime.metrics)
            unsupported_reason = None
        elif canonical and domain_lead in {"coordinator_agent", "", None}:
            domain_lead = lead_agent_for_hints(canonical, self.runtime.metrics)

        entities = list(classification.entities or [])
        if not entities and canonical:
            entities = await entities_from_catalogue(query, canonical[0], runtime=self.runtime, agent_id=self.agent_id)

        return {
            "query_class": query_class,
            "mission_lead": domain_lead,
            "initial_mission_lead": domain_lead,
            "entities": entities,
            "time_range": resolved.model_dump(),
            "metric_hints": merged_hints,
            "metric_id": preset_metric,
            "unsupported_reason": unsupported_reason,
            "task_graph": {"tasks": [{"id": task_id or f"T-{uuid4().hex[:8]}", "agent": "observer_agent"}]},
            "llm_calls": 1,
            "prompt_version": spec.version,
        }


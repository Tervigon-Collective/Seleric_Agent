"""Plan missions, route capabilities, and emit structured classification."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from seleric_swarm.agents.base import AgentContext, SwarmAgent
from seleric_swarm.contracts.lookup import CoordinatorClassificationV1
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
            catalogue_hints = await self._hints_from_catalogue(query)
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
            entities = await self._entities_from_catalogue(query, canonical[0])

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

    async def _hints_from_catalogue(self, query: str) -> list[str]:
        if "seleric.catalogue_search_metrics" not in self.runtime.mcp.capabilities:
            return []
        try:
            result = await self.runtime.mcp.call(
                agent_id=self.agent_id,
                capability="seleric.catalogue_search_metrics",
                arguments={"query": query},
            )
        except Exception:
            return []
        out: list[str] = []
        scored: list[tuple[int, str]] = []
        q_tokens = {
            tok
            for tok in re.findall(r"[a-z0-9]+", (query or "").lower())
            if tok not in {
                "what",
                "is",
                "the",
                "a",
                "an",
                "for",
                "on",
                "of",
                "and",
                "were",
                "was",
                "how",
                "many",
                "today",
                "yesterday",
                "last",
                "days",
                "in",
                "to",
            }
        }
        for match in result.get("matches") or []:
            how = str(match.get("matched_on") or "")
            if how.startswith("description"):
                continue
            registry_id = self.runtime.metrics.id_for_catalogue(match.get("id"))
            if not registry_id:
                continue
            hay = f"{match.get('id') or ''} {match.get('display_name') or ''}".lower().replace("_", " ")
            hay_tokens = set(re.findall(r"[a-z0-9]+", hay))
            overlap_tokens = hay_tokens & q_tokens
            overlap = len(overlap_tokens)
            cid = str(match.get("id") or "")
            if overlap >= 2:
                scored.append((overlap, registry_id))
            elif overlap == 1:
                tok = next(iter(overlap_tokens))
                if tok == cid or cid.split("_") == [tok]:
                    scored.append((overlap, registry_id))
        if not scored:
            return []
        best = max(item[0] for item in scored)
        for overlap, registry_id in scored:
            if overlap == best and registry_id not in out:
                out.append(registry_id)
        return out

    async def _entities_from_catalogue(self, query: str, metric_id: str) -> list[str]:
        definition = self.runtime.metrics.get(metric_id)
        if definition is None or "seleric.catalogue_get_metric" not in self.runtime.mcp.capabilities:
            return []
        try:
            payload = await self.runtime.mcp.call(
                agent_id=self.agent_id,
                capability="seleric.catalogue_get_metric",
                arguments={"metric_id": definition.catalogue_metric},
            )
        except Exception:
            return []
        supported = list(payload.get("supported_dimensions") or [])
        text = (query or "").lower()
        hits: list[str] = []
        for dim in supported:
            raw = str(dim)
            parts = [p for p in raw.removeprefix("lt_").split("_") if p]
            token = " ".join(parts)
            if raw.lower() in text or (token and token in text):
                hits.append(raw)
                continue
            if any(part in text for part in parts if len(part) >= 4):
                hits.append(raw)
        return hits

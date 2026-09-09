"""Per-mission LLM token/latency metering (docs/44 ticket PRD-002).

``TokenUsage``/``latency_ms`` were already returned per-call by every real
``LLMResponse`` but never summed anywhere. Wraps any ``LLMPort`` and
accumulates usage by ``request.metadata.mission_id`` so
``coordinator.governance.budget.check_swarm_budget`` can enforce
``MissionBudget.token_budget`` the same way it already enforces
``max_llm_calls``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

from seleric_swarm.llm.port import LLMPort, LLMRequest, LLMResponse, StructuredLLMResponse

T = TypeVar("T", bound=BaseModel)


@dataclass
class MissionUsage:
    llm_calls: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


class MeteredLLMPort:
    """Wraps a real ``LLMPort`` and accumulates usage per mission_id.

    # ponytail: in-memory dict, unbounded by mission count. Fine for a
    # single-process deployment where missions are short-lived; if usage
    # needs to survive a restart or span processes, move this into the
    # existing MissionStore instead of growing this dict further.
    """

    def __init__(self, inner: LLMPort) -> None:
        self._inner = inner
        self._usage: dict[str, MissionUsage] = {}

    def usage_for(self, mission_id: str | None) -> MissionUsage:
        return self._usage.get(mission_id or "", MissionUsage())

    def _record(self, request: LLMRequest, response: LLMResponse) -> None:
        mission_id = request.metadata.mission_id or ""
        entry = self._usage.setdefault(mission_id, MissionUsage())
        entry.llm_calls += 1
        entry.total_tokens += int(response.usage.total_tokens or 0)
        entry.latency_ms += float(response.latency_ms or 0.0)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await self._inner.complete(request)
        self._record(request, response)
        return response

    async def complete_structured(self, request: LLMRequest, schema: type[T]) -> StructuredLLMResponse:
        result = await self._inner.complete_structured(request, schema)
        self._record(request, result.raw)
        return result

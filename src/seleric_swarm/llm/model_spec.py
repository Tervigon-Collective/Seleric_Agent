"""Routing metadata for one candidate model behind the LLM gateway."""

from __future__ import annotations

from dataclasses import dataclass

from seleric_swarm.llm.port import LLMRequest


@dataclass(frozen=True)
class ModelSpec:
    id: str
    priority: int = 100
    supports_structured_output: bool = True
    supports_tool_calling: bool = False
    supports_vision: bool = False
    max_context_tokens: int = 128_000

    def supports(self, request: LLMRequest) -> bool:
        """Capability-aware routing gate: is this model even eligible for the request?"""
        if request.response_format == "json_schema" and not self.supports_structured_output:
            return False
        return True

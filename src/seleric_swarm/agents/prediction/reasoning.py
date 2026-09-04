"""LLM boundary for the Prediction Agent.

The reasoning model is used ONLY for narrative framing (scenario names, a
plain-language "what this means" sentence). It NEVER produces a number, an
interval, a horizon or a model choice. Every quantitative value comes from a
registered model or an approved statistical baseline.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from seleric_swarm.llm.port import ChatMessage, LLMPort, LLMRequest, LLMRequestMetadata


@runtime_checkable
class ReasoningModel(Protocol):
    async def generate_text(self, *, system: str, user: str, tags: list[str] | None = None) -> str: ...


class LLMPortReasoningModel:
    def __init__(self, port: LLMPort, *, model: str, mission_id: str = "", temperature: float = 0.0) -> None:
        self._port = port
        self._model = model
        self._mission_id = mission_id
        self._temperature = temperature

    async def generate_text(self, *, system: str, user: str, tags: list[str] | None = None) -> str:
        request = LLMRequest(
            messages=[ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
            model=self._model,
            temperature=self._temperature,
            max_tokens=512,
            metadata=LLMRequestMetadata(
                mission_id=self._mission_id, agent_id="prediction_agent", agent_version="1.0.0"
            ),
            tags=tags or ["prediction"],
        )
        return (await self._port.complete(request)).text


class NullReasoningModel:
    async def generate_text(self, *, system: str, user: str, tags: list[str] | None = None) -> str:
        return ""


class ScriptedReasoningModel:
    def __init__(self, text: list[str] | None = None) -> None:
        self._text = list(text or [])
        self.calls: list[dict[str, Any]] = []

    async def generate_text(self, *, system: str, user: str, tags: list[str] | None = None) -> str:
        self.calls.append({"user": user})
        return self._text.pop(0) if self._text else ""

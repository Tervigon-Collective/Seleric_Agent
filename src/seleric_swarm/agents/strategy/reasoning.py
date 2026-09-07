"""LLM boundary for the Strategy Agent.

Unlike Prediction (numbers only ever come from a model/baseline), an
intervention *option* is inherently a generative/qualitative judgment — there
is no "registered model" for what to do about a diagnosed mechanism. The
reasoning model is therefore the primary generator here, but it is always
constrained to the diagnosed mechanism the request carries: it cannot invent
a mechanism, only propose actions that address (or fail to address) the one
supplied. No mechanism supplied -> no generation call at all (see
``generation.py``), never a guess.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from seleric_swarm.llm.port import ChatMessage, LLMPort, LLMRequest, LLMRequestMetadata

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class ReasoningModel(Protocol):
    async def generate_structured(
        self, *, system: str, user: str, schema: type[T], tags: list[str] | None = None
    ) -> T: ...


class LLMPortReasoningModel:
    def __init__(self, port: LLMPort, *, model: str, mission_id: str = "", temperature: float = 0.0) -> None:
        self._port = port
        self._model = model
        self._mission_id = mission_id
        self._temperature = temperature

    async def generate_structured(
        self, *, system: str, user: str, schema: type[T], tags: list[str] | None = None
    ) -> T:
        request = LLMRequest(
            messages=[ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
            model=self._model,
            temperature=self._temperature,
            max_tokens=1024,
            metadata=LLMRequestMetadata(
                mission_id=self._mission_id, agent_id="strategy_agent", agent_version="1.0.0"
            ),
            tags=tags or ["strategy"],
        )
        result = await self._port.complete_structured(request, schema)
        return result.value  # type: ignore[return-value]


class NullReasoningModel:
    async def generate_structured(
        self, *, system: str, user: str, schema: type[T], tags: list[str] | None = None
    ) -> T:
        raise RuntimeError("No reasoning model configured for the Strategy Agent")


class ScriptedReasoningModel:
    """Deterministic test double. Returns queued objects in order."""

    def __init__(self, structured: list[Any] | None = None) -> None:
        self._structured = list(structured or [])
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(
        self, *, system: str, user: str, schema: type[T], tags: list[str] | None = None
    ) -> T:
        self.calls.append({"schema": schema.__name__, "user": user})
        if not self._structured:
            raise RuntimeError("ScriptedReasoningModel exhausted")
        return self._structured.pop(0)

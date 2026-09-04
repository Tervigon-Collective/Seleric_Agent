"""LLM boundary for the Skeptic.

The Skeptic uses a reasoning model for *semantic* tasks only (alternative
hypothesis phrasing, challenge planning hints, contradiction interpretation,
final human-readable explanation). Every deterministic validator, the risk
scorer, the trust scorer and the verdict engine run with no model at all, so a
model outage degrades the explanation, never the verdict.
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

    async def generate_text(self, *, system: str, user: str, tags: list[str] | None = None) -> str: ...


class LLMPortReasoningModel:
    """Adapts the repo's :class:`LLMPort` to :class:`ReasoningModel`."""

    def __init__(self, port: LLMPort, *, model: str, mission_id: str = "", temperature: float = 0.0) -> None:
        self._port = port
        self._model = model
        self._mission_id = mission_id
        self._temperature = temperature

    def _request(self, system: str, user: str, tags: list[str] | None) -> LLMRequest:
        return LLMRequest(
            messages=[ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
            model=self._model,
            temperature=self._temperature,
            max_tokens=1024,
            metadata=LLMRequestMetadata(
                mission_id=self._mission_id, agent_id="skeptic_agent", agent_version="1.0.0"
            ),
            tags=tags or ["skeptic"],
        )

    async def generate_structured(
        self, *, system: str, user: str, schema: type[T], tags: list[str] | None = None
    ) -> T:
        result = await self._port.complete_structured(self._request(system, user, tags), schema)
        return result.value  # type: ignore[return-value]

    async def generate_text(self, *, system: str, user: str, tags: list[str] | None = None) -> str:
        result = await self._port.complete(self._request(system, user, tags))
        return result.text


class NullReasoningModel:
    """No-LLM fallback. Structured calls raise; text calls return ""."""

    async def generate_structured(
        self, *, system: str, user: str, schema: type[T], tags: list[str] | None = None
    ) -> T:
        raise RuntimeError("No reasoning model configured for the Skeptic")

    async def generate_text(self, *, system: str, user: str, tags: list[str] | None = None) -> str:
        return ""


class ScriptedReasoningModel:
    """Deterministic test double. Returns queued objects / strings in order."""

    def __init__(self, structured: list[Any] | None = None, text: list[str] | None = None) -> None:
        self._structured = list(structured or [])
        self._text = list(text or [])
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(
        self, *, system: str, user: str, schema: type[T], tags: list[str] | None = None
    ) -> T:
        self.calls.append({"kind": "structured", "user": user, "schema": schema.__name__})
        if not self._structured:
            raise RuntimeError("ScriptedReasoningModel exhausted")
        return self._structured.pop(0)

    async def generate_text(self, *, system: str, user: str, tags: list[str] | None = None) -> str:
        self.calls.append({"kind": "text", "user": user})
        return self._text.pop(0) if self._text else ""

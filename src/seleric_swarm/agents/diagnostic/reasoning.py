"""LLM boundary for the Diagnostic Agent.

The reasoning model is used ONLY for semantic hypothesis generation and mechanism
phrasing. It never decides retain/reject, never estimates an effect, never emits
a root cause. Observation-seeded hypotheses are always present; the LLM only
adds constrained, evidence-bounded alternatives.
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
    """LLM port wrapper that propagates full trace metadata to LangSmith."""

    def __init__(
        self,
        port: LLMPort,
        *,
        model: str,
        mission_id: str = "",
        request_id: str | None = None,
        session_id: str | None = None,
        workflow_name: str | None = None,
        workflow_version: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        self._port = port
        self._model = model
        self._mission_id = mission_id
        self._request_id = request_id
        self._session_id = session_id
        self._workflow_name = workflow_name
        self._workflow_version = workflow_version
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
                mission_id=self._mission_id,
                request_id=self._request_id,
                session_id=self._session_id,
                workflow_name=self._workflow_name,
                workflow_version=self._workflow_version,
                agent_id="diagnostic_agent",
                agent_version="1.0.0",
            ),
            tags=tags or ["diagnostic"],
        )
        result = await self._port.complete_structured(request, schema)
        return result.value  # type: ignore[return-value]


class NullReasoningModel:
    async def generate_structured(
        self, *, system: str, user: str, schema: type[T], tags: list[str] | None = None
    ) -> T:
        raise RuntimeError("No reasoning model configured for the Diagnostic Agent")


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

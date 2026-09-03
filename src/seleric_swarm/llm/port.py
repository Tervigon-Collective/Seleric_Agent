from __future__ import annotations

from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field

from seleric_swarm.llm.errors import LLMError

T = TypeVar("T", bound=BaseModel)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequestMetadata(BaseModel):
    request_id: str | None = None
    session_id: str | None = None
    mission_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    agent_version: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    model: str | None = None
    query_class: str | None = None


class LLMRequest(BaseModel):
    messages: list[ChatMessage]
    model: str
    temperature: float = 0.0
    max_tokens: int = 1024
    timeout_s: float = 30.0
    response_format: Literal["text", "json_schema"] = "text"
    metadata: LLMRequestMetadata = Field(default_factory=LLMRequestMetadata)
    tags: list[str] = Field(default_factory=list)
    fallback_model: str | None = None


class TokenUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class LLMResponse(BaseModel):
    text: str
    parsed: dict[str, Any] | None = None
    model: str
    finish_reason: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    retry_count: int = 0
    provider_request_id: str | None = None
    normalized_error: LLMError | None = None

    model_config = {"arbitrary_types_allowed": True}


class StructuredLLMResponse(BaseModel):
    value: Any
    raw: LLMResponse

    model_config = {"arbitrary_types_allowed": True}


class LLMPort(Protocol):
    """Provider-independent LLM boundary. Agents must depend on this, not Azure SDKs."""

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def complete_structured(
        self, request: LLMRequest, schema: type[T]
    ) -> StructuredLLMResponse: ...

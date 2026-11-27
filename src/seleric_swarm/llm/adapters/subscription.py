"""Helper-model calls for a ChatGPT plan sign-in or the Claude Code CLI."""

from __future__ import annotations

import json
import time

import httpx

from seleric_swarm.llm.chatgpt_plan import RESOURCE, access_token
from seleric_swarm.llm.claude_code import run as claude_run
from seleric_swarm.llm.errors import LLMError, LLMErrorCode
from seleric_swarm.llm.models_store import ModelsError
from seleric_swarm.llm.port import LLMRequest, LLMResponse, StructuredLLMResponse
from seleric_swarm.llm.structured import parse_structured, with_schema_instruction


def text_from_sse(payload: str) -> str:
    """Join Responses API text deltas. A failed response raises before any partial text is returned."""
    chunks: list[str] = []
    for line in payload.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "response.output_text.delta":
            chunks.append(str(event.get("delta") or ""))
        elif kind == "response.failed":
            error = (event.get("response") or {}).get("error") or {}
            code = str(error.get("code") or "")
            retryable = code in {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}
            if code == "subscription_sharing_usage_limit_exceeded":
                retryable = True
            raise LLMError(LLMErrorCode.UNAVAILABLE, str(error.get("message") or code or "ChatGPT plan request failed"), retryable=retryable)
    return "".join(chunks)


class ChatGPTPlanAdapter:
    def __init__(self, connection_id: str) -> None:
        self._connection_id = connection_id

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            token = access_token(self._connection_id)
        except ModelsError as exc:
            raise LLMError(LLMErrorCode.AUTH, str(exc)) from exc
        body = {
            "model": request.model,
            "input": [{"role": message.role, "content": message.content} for message in request.messages],
            "store": False,
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(timeout=request.timeout_s) as client:
                response = await client.post(
                    f"{RESOURCE}/responses",
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise LLMError(LLMErrorCode.TIMEOUT, "ChatGPT plan request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise LLMError(LLMErrorCode.UNAVAILABLE, "ChatGPT plan request failed", retryable=True) from exc
        if response.status_code in (401, 403):
            raise LLMError(LLMErrorCode.AUTH, "ChatGPT rejected this sign-in")
        if response.status_code == 429:
            raise LLMError(LLMErrorCode.RATE_LIMIT, "ChatGPT plan limit reached", retryable=True)
        if response.status_code >= 400:
            raise LLMError(LLMErrorCode.UNAVAILABLE, f"ChatGPT answered {response.status_code}", retryable=response.status_code >= 500)
        return LLMResponse(text=text_from_sse(response.text), model=request.model, latency_ms=(time.perf_counter() - started) * 1000)

    async def complete_structured(self, request: LLMRequest, schema: type) -> StructuredLLMResponse:
        raw = await self.complete(with_schema_instruction(request, schema))
        return StructuredLLMResponse(value=parse_structured(raw, schema), raw=raw)


class ClaudeCodeAdapter:
    async def complete(self, request: LLMRequest) -> LLMResponse:
        import asyncio

        started = time.perf_counter()
        system = "\n\n".join(message.content for message in request.messages if message.role == "system")
        prompt = "\n\n".join(message.content for message in request.messages if message.role != "system")
        try:
            text = await asyncio.to_thread(claude_run, request.model, prompt, system=system, timeout_s=request.timeout_s)
        except ModelsError as exc:
            message = str(exc)
            code = LLMErrorCode.TIMEOUT if "timed out" in message else LLMErrorCode.UNAVAILABLE
            raise LLMError(code, message, retryable=code == LLMErrorCode.TIMEOUT) from exc
        return LLMResponse(text=text, model=request.model, latency_ms=(time.perf_counter() - started) * 1000)

    async def complete_structured(self, request: LLMRequest, schema: type) -> StructuredLLMResponse:
        raw = await self.complete(with_schema_instruction(request, schema))
        return StructuredLLMResponse(value=parse_structured(raw, schema), raw=raw)

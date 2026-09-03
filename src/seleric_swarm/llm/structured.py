from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from seleric_swarm.llm.errors import LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMResponse

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    fenced = _FENCE_RE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMStructuredOutputError("LLM response did not contain a JSON object")
    try:
        value = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMStructuredOutputError(f"LLM JSON parse failed: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMStructuredOutputError("LLM JSON was not an object")
    return value


def schema_instruction(schema: type[BaseModel]) -> str:
    payload = json.dumps(schema.model_json_schema(), indent=2)
    return (
        "Return ONLY a JSON object that validates against this JSON Schema. "
        "Do not wrap it in markdown. Do not include commentary.\n"
        f"{payload}"
    )


def with_schema_instruction(request: LLMRequest, schema: type[BaseModel]) -> LLMRequest:
    instruction = schema_instruction(schema)
    messages = list(request.messages)
    if messages and messages[0].role == "system":
        messages[0] = ChatMessage(
            role="system",
            content=f"{messages[0].content.rstrip()}\n\n{instruction}",
        )
    else:
        messages.insert(0, ChatMessage(role="system", content=instruction))
    return request.model_copy(
        update={"messages": messages, "response_format": "json_schema"}
    )


def parse_structured(raw: LLMResponse, schema: type[T]) -> T:
    payload = extract_json_object(raw.text)
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise LLMStructuredOutputError(str(exc), retry_count=raw.retry_count) from exc

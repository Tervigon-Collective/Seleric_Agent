"""pydantic-ai implementation of ``seleric_swarm.answer.AnswerLLM`` (structured ``AnswerDraft``)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model

from seleric_swarm.contracts.pipeline import AnswerDraft


class PydanticAiAnswerLLM:
    def __init__(self, model: Model) -> None:
        self._model = model

    async def __call__(self, *, instructions: str, prompt: str) -> tuple[AnswerDraft, dict[str, Any]]:
        writer: Agent[None, AnswerDraft] = Agent(
            model=self._model, output_type=AnswerDraft, instructions=instructions, name="seleric_answer_v2"
        )
        result = await writer.run(prompt)
        usage = result.usage() if callable(result.usage) else result.usage
        return result.output, {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "model": getattr(self._model, "model_name", type(self._model).__name__),
        }

"""pydantic-ai implementation of ``seleric_swarm.understand.DraftLLM``.

The understand package stays free of LLM SDKs (A1); this adapter owns the model call and
asks for structured output, returning either a ``QuestionDraft`` or a ``Clarification``.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model

from seleric_swarm.contracts.pipeline import Clarification, QuestionDraft


class PydanticAiDraftLLM:
    """``DraftLLM`` over a pydantic-ai ``Model`` (any provider the agent's model chain supports)."""

    def __init__(self, model: Model) -> None:
        self._model = model

    async def __call__(self, *, instructions: str, prompt: str) -> tuple[QuestionDraft | Clarification, dict[str, Any]]:
        reader: Agent[None, QuestionDraft | Clarification] = Agent(
            model=self._model,
            output_type=[QuestionDraft, Clarification],
            instructions=instructions,
            name="seleric_understand_v2",
        )
        result = await reader.run(prompt)
        usage = result.usage() if callable(result.usage) else result.usage
        return result.output, {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "model": getattr(self._model, "model_name", type(self._model).__name__),
        }

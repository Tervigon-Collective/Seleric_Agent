"""Understand v2 (M1.1): one structured LLM call turns the question into a ``QuestionDraft``.

Public interface: ``understand`` and ``DraftLLM`` (the injected model call). The draft and
clarification types are stage-boundary contracts in ``seleric_swarm.contracts.pipeline``.
The pydantic-ai implementation of ``DraftLLM`` is ``seleric_swarm.adapters.understand_llm``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from seleric_swarm.contracts.pipeline import Clarification, QuestionDraft
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.understand._call import DraftLLM, call_understand
from seleric_swarm.understand._prompt import INSTRUCTIONS, build_prompt

__all__ = ["DraftLLM", "understand"]


async def understand(
    llm: DraftLLM | None,
    question: str,
    *,
    read_model: SemanticReadModel,
    value_words: Sequence[str] = (),
    value_meanings: Mapping[str, str] | None = None,
    prior_question: str = "",
    prior_offer: str = "",
    exemplars: Sequence[Mapping[str, Any]] = (),
    instructions: str | None = None,
) -> tuple[QuestionDraft | Clarification, dict[str, Any]]:
    """Read ``question`` into a draft, or a clarification (A4: one retry, then ask).

    Returns ``(draft_or_clarification, stats)``; never raises.
    """
    prompt = build_prompt(
        question,
        read_model=read_model,
        value_words=list(value_words),
        value_meanings=value_meanings,
        prior_question=prior_question,
        prior_offer=prior_offer,
        exemplars=[dict(e) for e in exemplars],
    )
    # ``instructions``: a versioned prompt from the registry (pipeline); the built-in text otherwise.
    return await call_understand(llm, instructions=instructions or INSTRUCTIONS, prompt=prompt)

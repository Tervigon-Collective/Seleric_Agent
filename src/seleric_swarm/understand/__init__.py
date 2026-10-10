"""Understand v2 (M1.1) — one structured LLM call producing a QuestionDraft.

Public interface: `understand`, `QuestionDraft`, `Clarification`, `WindowExpr`, `MeasureExpr`, `UnderstandOutput`.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic_ai.models import Model

from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.understand._call import call_understand
from seleric_swarm.understand._prompt import build_prompt
from seleric_swarm.understand.draft import (
    Clarification,
    MeasureExpr,
    QuestionDraft,
    UnderstandOutput,
    WindowExpr,
)

__all__ = [
    "QuestionDraft",
    "Clarification",
    "UnderstandOutput",
    "WindowExpr",
    "MeasureExpr",
    "understand",
]


async def understand(
    model: Model | None,
    query: str,
    *,
    read_model: SemanticReadModel,
    value_words: list[str] | None = None,
    value_meanings: Mapping[str, str] | None = None,
    prior_question: str = "",
    prior_offer: str = "",
    exemplars: list[dict[str, Any]] | None = None,
) -> tuple[UnderstandOutput, dict[str, Any]]:
    """One structured LLM call producing a QuestionDraft.

    Returns (UnderstandOutput, stats_dict).
    UnderstandOutput wraps either QuestionDraft or Clarification.
    On failure after one retry, returns UnderstandOutput.from_clarification(...).
    """
    prompt = build_prompt(
        query,
        read_model=read_model,
        value_words=list(value_words or ()),
        value_meanings=value_meanings,
        prior_question=prior_question,
        prior_offer=prior_offer,
        exemplars=exemplars,
    )
    return await call_understand(model, prompt, read_model=read_model)
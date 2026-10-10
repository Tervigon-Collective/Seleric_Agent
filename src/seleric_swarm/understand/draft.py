"""QuestionDraft — the structured output of the understand LLM call (M1.1).

This is the v2 replacement for Understanding: phrases and expressions only,
no dates, no ids required. The spec compiler (M1.2) resolves ids and computes
dates from the draft expressions.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field


QuestionKind = Literal[
    "conversation",
    "analysis",
    "overview",
    "forecast",
    "what_if",
    "action",
    "exploration",
]


class WindowExpr(BaseModel):
    """A time window expression from the user's message (not a calendar date)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["event", "baseline", "context"]
    # The user's words: "yesterday", "last 7 days", "this month", "the 3 days before"
    expr: str
    # Optional explicit bounds if the user named them: "2026-10-01..2026-10-07"
    start_iso: str | None = None
    end_iso: str | None = None
    # When the user says "till 11 am" / "through hour 15"
    through_hour: int | None = None


class MeasureExpr(BaseModel):
    """A measure the user asked for, in their own words."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    phrase: str  # user's words: "net sales", "ROAS", "orders"
    # Optional: if the model is confident about the catalogue id
    metric_id: str | None = None


class QuestionDraft(BaseModel):
    """Structured understanding of the user's question.

    This is the output_type of the understand LLM call. It contains phrases
    and expressions only — no resolved ids, no calendar dates. The spec
    compiler (M1.2) does all resolution against the catalogue read model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1

    # Classification
    kind: QuestionKind
    shape: str  # from plan template registry (M2.1), not hardcoded

    # Measures the user asked for
    measures: tuple[MeasureExpr, ...]

    # Named values the user used as filters/breakdowns (quoted from the message)
    filter_phrases: tuple[str, ...] = ()
    breakdown_phrases: tuple[str, ...] = ()

    # Explicit exclusion phrases: "exclude meta", "without google"
    exclusion_phrases: tuple[str, ...] = ()

    # Entity phrases: "campaign X", "product Y" — the model extracts the names
    entity_phrases: tuple[str, ...] = ()

    # Time windows as expressions
    windows: tuple[WindowExpr, ...] = ()

    # Optional grain the user named: "by day", "weekly"
    grain: str | None = None

    # Ranking: "top 5 by ROAS"
    ranking: str | None = None
    ranking_order: Literal["desc", "asc"] = "desc"
    ranking_limit: int | None = None

    # Follow-up / context
    follows_prior: bool = False
    accepts_offer: bool = False
    restyles_prior: bool = False

    # Ambiguity the model detected but couldn't resolve
    needs_clarification: str | None = None

    # Ordinary words that look like data values but are plain language
    ordinary_words: tuple[str, ...] = ()


class Clarification(BaseModel):
    """The understand call returns this when it cannot resolve ambiguity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str
    question_to_user: str
    options: tuple[str, ...] = ()


# Union type for the LLM output - use a wrapper to satisfy pydantic_ai
class UnderstandOutput(BaseModel):
    """Wrapper for the understand LLM output: either QuestionDraft or Clarification."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Discriminated union via a type field
    type: Literal["question_draft", "clarification"]
    question_draft: QuestionDraft | None = None
    clarification: Clarification | None = None

    @classmethod
    def from_question_draft(cls, draft: QuestionDraft) -> UnderstandOutput:
        return cls(type="question_draft", question_draft=draft)

    @classmethod
    def from_clarification(cls, clar: Clarification) -> UnderstandOutput:
        return cls(type="clarification", clarification=clar)

    def is_clarification(self) -> bool:
        return self.type == "clarification"

    def is_question_draft(self) -> bool:
        return self.type == "question_draft"

    def get_question_draft(self) -> QuestionDraft | None:
        return self.question_draft

    def get_clarification(self) -> Clarification | None:
        return self.clarification
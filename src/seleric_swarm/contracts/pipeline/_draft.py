"""QuestionDraft — the understand stage's output and the spec stage's input.

A stage boundary, so it lives here with the other frozen contracts (A3): ``understand``
produces it, ``spec`` consumes it, and neither imports the other (A1). Phrases and
structured expressions only — no calendar dates (code computes them from ``as_of``),
no required catalogue ids (``spec`` resolves them). Added in contracts-v1.1 (additive;
no existing model changed).
"""

from __future__ import annotations

from typing import Literal

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
PlanShape = Literal[
    "lookup",
    "trend",
    "breakdown",
    "period_comparison",
    "entity_comparison",
    "why_single_metric",
    "funnel",
    "composition",
    "other",
]
WindowRole = Literal["event", "baseline", "context"]
WindowUnit = Literal["day", "week", "month", "quarter", "year"]
WindowEnding = Literal["yesterday", "today", "as_of", "before_event"]
WindowName = Literal[
    "",
    "yesterday",
    "today",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "this_year",
    "last_year",
]
HorizonUnit = Literal["day", "week", "month"]
PeriodWord = Literal["next_n", "rest_of_month", "next_month", "this_quarter"]


class WindowExpr(BaseModel):
    """A date expression. Code turns this into calendar dates against as_of."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: WindowName = ""
    unit: WindowUnit | Literal[""] = ""
    n: int | None = None
    ending: WindowEnding = "yesterday"
    start_iso: str = ""
    end_iso: str = ""


class WindowSlot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: WindowRole
    expr: WindowExpr = Field(default_factory=WindowExpr)
    source_span: str = ""


class MeasurePhrase(BaseModel):
    """One measure the user asked for, in their words (id optional until resolve)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    words: str
    metric_id: str | None = None
    axes: tuple[tuple[str, str], ...] = ()


class ForecastHorizonSlot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int = 14
    unit: HorizonUnit = "day"
    until_iso: str = ""
    period_word: PeriodWord = "next_n"


class ForecastSlots(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    targets: tuple[MeasurePhrase, ...] = ()
    horizon: ForecastHorizonSlot = Field(default_factory=ForecastHorizonSlot)


class QuestionDraft(BaseModel):
    """Structured reading of the question. No calendar dates; no required catalogue ids."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: QuestionKind = "analysis"
    shape: PlanShape = "other"
    measures: tuple[MeasurePhrase, ...] = ()
    entity_dimension: str = ""
    entity_phrases: tuple[str, ...] = ()
    exclusion_phrases: tuple[str, ...] = ()
    breakdown_dimensions: tuple[str, ...] = ()
    breakdown_words: str = ""
    windows: tuple[WindowSlot, ...] = ()
    grain: Literal["hour", "day", "week", "month", "none"] = "none"
    direction: Literal["increase", "decrease", "either"] = "either"
    rank_by: MeasurePhrase | None = None
    rank_order: Literal["desc", "asc"] = "desc"
    top_n: int | None = None
    ordinary_words: tuple[str, ...] = ()
    names_period: bool = False
    through_hour: int | None = None
    follows_prior: bool = False
    accepts_offer: bool = False
    restyles_prior: bool = False
    part_of_whole: bool = False
    forecast: ForecastSlots | None = None
    reply: str = ""
    needs_clarification: str | None = None

"""Question-side pipeline contracts (windows, filters, QuestionSpec)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Ref(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["evidence", "derivation", "finding", "result_set"]
    id: str


class WindowSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["event", "baseline", "context"]
    start: date
    end: date
    source_span: str = ""
    token: str = ""
    through_hour: int | None = None


class FilterSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    operator: Literal["in", "not_in"]
    values: tuple[str, ...]
    source_span: str = ""
    volume: int | None = None  # not_in = exclusions ("exclude exchanges")


class EntitySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    values: tuple[str, ...]
    source_span: str = ""


class MeasureSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phrase: str
    metric_id: str | None
    axes: tuple[tuple[str, str], ...] = ()


class RankingSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    by_metric_id: str
    order: Literal["asc", "desc"]
    limit: int


class QuestionSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    kind: Literal[
        "conversation",
        "analysis",
        "overview",
        "forecast",
        "what_if",
        "action",
        "exploration",
    ]
    shape: str  # values come from plan template registry (M2.1), not hardcoded here
    measures: tuple[MeasureSpec, ...]
    filters: tuple[FilterSpec, ...]
    entities: tuple[EntitySpec, ...]
    breakdowns: tuple[str, ...]
    windows: tuple[WindowSpec, ...]
    grain: str | None
    ranking: RankingSpec | None
    direction: Literal["up", "down", "either"] = "either"
    follow_up_of: str | None = None  # prior mission id when this is an edit
    assumptions: tuple[str, ...] = ()
    catalogue_version: str


class Clarification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str
    question_to_user: str
    options: tuple[str, ...] = ()

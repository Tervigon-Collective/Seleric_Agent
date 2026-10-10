"""Plan-side pipeline contracts (query / engine / derive steps)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from seleric_swarm.contracts.pipeline._question import FilterSpec, RankingSpec, Ref


class QueryStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    metric_id: str
    breakdowns: tuple[str, ...]
    filters: tuple[FilterSpec, ...]
    window_role: Literal["event", "baseline", "context"]
    grain: str | None
    ranking: RankingSpec | None = None
    entities_from_step: str | None = None
    elapsed_only: bool = False


class EngineStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    engine: str
    params: dict[str, JsonValue]
    inputs: tuple[str, ...] = ()


class DeriveStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    op: str
    inputs: tuple[Ref | str, ...]
    params: dict[str, JsonValue] = {}


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    template_id: str
    steps: tuple[QueryStep | EngineStep | DeriveStep, ...]
    needs_agent: bool = False
    trusted: bool = False  # trusted = built from a verified template/example (M1.3)

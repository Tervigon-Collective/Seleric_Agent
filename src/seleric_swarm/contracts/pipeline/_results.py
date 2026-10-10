"""Result and derivation pipeline contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from seleric_swarm.contracts.pipeline._errors import StageError
from seleric_swarm.contracts.pipeline._plan import JsonValue
from seleric_swarm.contracts.pipeline._question import FilterSpec, Ref, WindowSpec


class ResultRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    dimensions: dict[str, str]
    value: float | None


class ResultSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: Ref
    step_id: str
    metric_id: str
    unit: str | None
    window: WindowSpec
    breakdowns: tuple[str, ...]
    filters: tuple[FilterSpec, ...]
    rows: tuple[ResultRow, ...]
    error: StageError | None = None


class Derivation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: Ref
    op: str
    inputs: tuple[Ref, ...]
    params: dict[str, JsonValue]
    value: float | None
    unit: str | None
    rows: tuple[ResultRow, ...] = ()
    error: StageError | None = None

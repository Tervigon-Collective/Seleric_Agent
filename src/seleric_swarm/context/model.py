"""Context contracts (M4.1): events and data health that diagnosis can cite."""

from __future__ import annotations

from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.semantic import SemanticReadModel


class ContextWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: date
    end: date

    def overlaps(self, start: date, end: date) -> bool:
        return start <= self.end and end >= self.start


class ContextEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    start: date
    end: date
    scope: dict[str, str] = Field(default_factory=dict)
    source: str
    title: str
    detail: str = ""
    metric_ids: tuple[str, ...] = ()  # empty = every metric


class DataHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    day: date | None = None
    status: Literal["ok", "late", "incomplete", "incident"]
    detail: str = ""


class ContextProvider(Protocol):
    id: str

    def events(
        self, window: ContextWindow, scope: dict[str, str], read_model: SemanticReadModel
    ) -> list[ContextEvent]: ...


class HealthSource(Protocol):
    id: str

    def health(
        self, window: ContextWindow, metric_ids: tuple[str, ...], read_model: SemanticReadModel
    ) -> list[DataHealth]: ...


class AsyncContextProvider(Protocol):
    """A provider that reads data (through a port) to find its events."""

    id: str

    async def aevents(
        self, window: ContextWindow, scope: dict[str, str], read_model: SemanticReadModel
    ) -> list[ContextEvent]: ...

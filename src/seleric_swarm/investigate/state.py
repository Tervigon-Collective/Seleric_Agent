"""Investigation contracts (M4.3): request, policy, findings and the persisted state."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.context import ContextEvent, DataHealth
from seleric_swarm.contracts.pipeline import FilterSpec, WindowSpec

Stage = Literal["premise", "health", "decompose", "localise", "recurse", "context", "drivers", "stop"]


class InvestigateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    event: WindowSpec
    baseline: WindowSpec
    filters: tuple[FilterSpec, ...] = ()
    direction: Literal["up", "down", "either"] = "either"


class InvestigatePolicy(BaseModel):
    """Thresholds (callers load them from policy_config; defaults per the plan)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recurse_share: float = 0.5
    max_depth: int = 3
    explained_target: float = 0.8
    min_change_ratio: float = 0.02  # |Δ| / |baseline| below this = no material change
    max_reads: int = 60


class Segment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    value: str
    baseline: float
    event: float
    delta: float
    share_of_change: float  # delta / parent delta


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["premise", "health", "component", "segment", "context", "driver", "limitation"]
    stage: Stage
    depth: int = 0
    subject: str = ""  # metric id or dimension=value path
    baseline: float | None = None
    event: float | None = None
    delta: float | None = None
    share_of_change: float | None = None
    # driver: "supported_cause" only when the cause moved materially in its expected direction.
    verdict: str = ""
    detail: str = ""
    path: tuple[FilterSpec, ...] = ()


class InvestigationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    request: InvestigateRequest
    stage: Stage = "premise"
    status: Literal["running", "done", "premise_false", "unhealthy", "budget"] = "running"
    baseline_total: float | None = None
    event_total: float | None = None
    explained_share: float = 0.0
    path: tuple[FilterSpec, ...] = ()
    depth: int = 0
    reads: int = 0
    findings: list[Finding] = Field(default_factory=list)
    events: list[ContextEvent] = Field(default_factory=list)
    health: list[DataHealth] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @property
    def change(self) -> float | None:
        if self.baseline_total is None or self.event_total is None:
            return None
        return self.event_total - self.baseline_total


class MetricReader(Protocol):
    """Read port the controller queries (pipeline adapts the semantic port / executor)."""

    async def total(self, metric_id: str, window: WindowSpec, filters: tuple[FilterSpec, ...]) -> float | None: ...

    async def breakdown(
        self, metric_id: str, window: WindowSpec, dimension: str, filters: tuple[FilterSpec, ...]
    ) -> dict[str, float]: ...


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    summary: str
    share_of_change: float


class Selector(Protocol):
    """Chooses among typed candidates at a branch point (an LLM in production). Never edits numbers."""

    async def __call__(self, *, stage: Stage, candidates: list[Candidate]) -> tuple[str, str]: ...

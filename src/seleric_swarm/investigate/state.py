"""Investigation contracts (M4.3): request, policy, findings and the persisted state."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.context import ContextEvent, DataHealth
from seleric_swarm.contracts.pipeline import FilterSpec, WindowSpec

Stage = Literal["premise", "health", "decompose", "localise", "recurse", "context", "drivers", "stop"]

# Aggregations whose slices do not sum to the total. Summing an average, ratio, rate
# or distinct count across rows would invent a number, so readers must not do it
# (AG-01). One definition, shared by the investigation controller and the pipeline
# reader so the two cannot drift.
NON_ADDITIVE_AGGREGATIONS = frozenset(
    {"avg", "average", "mean", "median", "ratio", "rate", "percent", "percentage",
     "count_distinct", "distinct", "unique", "min", "max", "last", "first"}
)


def aggregation_is_additive(aggregation: str | None) -> bool:
    """Whether per-slice values of a metric with this aggregation can be summed."""
    return (aggregation or "").strip().lower() not in NON_ADDITIVE_AGGREGATIONS


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
    max_dimensions_per_level: int = 5  # cap breadth at each localisation level


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
    # How much trust the finding carries (AG-02): exact accounting, observed
    # segment contribution, or a hypothesis at some strength. Direction agreement
    # alone never becomes "causally_supported".
    qualification: Literal[
        "exact_accounting",
        "observed_segment_contribution",
        "directionally_consistent_hypothesis",
        "untested_hypothesis",
        "contradicted_hypothesis",
        "causally_supported",
    ] = "untested_hypothesis"


class InvestigationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # v2 (AG-01): premise outcomes are split into ``not_material`` (no material
    # change) and ``insufficient_evidence`` (a window could not be read), which
    # were both previously collapsed into ``premise_false``. See ADR-007.
    schema_version: int = 2
    request: InvestigateRequest
    stage: Stage = "premise"
    status: Literal[
        "running", "done", "premise_false", "not_material", "insufficient_evidence", "unhealthy", "budget"
    ] = "running"
    baseline_total: float | None = None
    event_total: float | None = None
    explained_share: float = 0.0
    unexplained_residual: float | None = None  # change not accounted for by components (AG-02)
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


class Attributor(Protocol):
    """Optional causal attribution of the outcome's change to graph nodes (e.g. DoWhy-GCM)."""

    async def __call__(
        self, *, target: str, baseline: WindowSpec, event: WindowSpec, filters: tuple[FilterSpec, ...]
    ) -> dict[str, float]: ...

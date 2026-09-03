"""Provider ports - the seams you plug real data / models into (architecture sec. 3, 5, 10-13).

Every provider is a ``typing.Protocol``. The prototype ships ``Fixture*`` /
``Template*`` implementations that are deterministic and make **zero** LLM or
network calls, and stamp ``data_origin`` / ``synthetic`` on everything they
return. Swap them for MCP- and model-backed implementations without touching any
agent code.

    DataProvider      domain data retrieval (later: MCP-backed)
    AnomalyDetector   what changed unusually (later: STL / IsolationForest / ...)
    CausalEngine      why it changed (later: DoWhy / EconML)
    Forecaster        what happens next (later: registered forecast models)
    Optimizer         which intervention is best (later: rules / optimizer)
    StatsEngine       significance / refutation helpers (later: statsmodels / scipy)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Result records - stable shapes agents consume regardless of implementation
# --------------------------------------------------------------------------- #


@dataclass
class MetricReading:
    metric_id: str
    value: float | None
    baseline: float | None = None
    unit: str | None = None
    dimensions: dict[str, Any] = field(default_factory=dict)
    direction_bad: str = "up"
    data_origin: str = "TEMPLATE"
    synthetic: bool = True
    source: str = "template"

    @property
    def change_pct(self) -> float | None:
        if self.value is None or not self.baseline:
            return None
        return round((self.value - self.baseline) / self.baseline * 100.0, 2)


@dataclass
class DomainEvent:
    event_id: str
    type: str
    domain: str
    at: str
    description: str = ""
    data_origin: str = "TEMPLATE"
    synthetic: bool = True


@dataclass
class DataResult:
    readings: list[MetricReading] = field(default_factory=list)
    events: list[DomainEvent] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    data_origin: str = "TEMPLATE"
    synthetic: bool = True


@dataclass
class AnomalyFinding:
    metric_id: str
    observed: float | None
    expected_range: list[float]
    deviation_pct: float | None
    score: float
    direction: str
    detector: dict[str, Any] = field(default_factory=dict)
    dimensions: dict[str, Any] = field(default_factory=dict)
    start_time: str | None = None
    data_origin: str = "TEMPLATE"
    synthetic: bool = True


@dataclass
class CausalQuery:
    treatment: str
    outcome: str
    common_causes: list[str]
    graph_id: str
    estimator: str = "backdoor.linear_regression"
    refuters: list[str] = field(default_factory=lambda: ["random_common_cause", "placebo_treatment"])


@dataclass
class CausalResult:
    treatment: str
    outcome: str
    effect: float | None
    effect_ci: list[float]
    refutations: list[dict[str, Any]]
    passed: bool
    estimator: str = ""
    graph_id: str = ""
    data_origin: str = "TEMPLATE"
    synthetic: bool = True


@dataclass
class ForecastResult:
    target: str
    horizon: str
    prediction: Any
    interval: list[float]
    model: dict[str, Any]
    drift_status: str | None = None
    secondary: dict[str, Any] = field(default_factory=dict)
    data_origin: str = "TEMPLATE"
    synthetic: bool = True


@dataclass
class RankedInterventions:
    options: list[dict[str, Any]]
    recommended: list[str]
    data_origin: str = "TEMPLATE"
    synthetic: bool = True


@dataclass
class StatResult:
    name: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)
    data_origin: str = "TEMPLATE"
    synthetic: bool = True


# --------------------------------------------------------------------------- #
# Protocols
# --------------------------------------------------------------------------- #


@runtime_checkable
class DataProvider(Protocol):
    domain: str

    async def fetch(
        self,
        *,
        metric_ids: list[str],
        time_range: dict[str, Any],
        dimensions: dict[str, Any] | None = None,
    ) -> DataResult: ...

    async def events(self, *, time_range: dict[str, Any]) -> list[DomainEvent]: ...


@runtime_checkable
class AnomalyDetector(Protocol):
    async def detect(self, readings: list[MetricReading], *, context: dict[str, Any]) -> list[AnomalyFinding]: ...


@runtime_checkable
class CausalEngine(Protocol):
    async def estimate(self, query: CausalQuery, *, context: dict[str, Any]) -> CausalResult: ...


@runtime_checkable
class Forecaster(Protocol):
    async def forecast(
        self, *, target: str, horizon: str, features: dict[str, Any]
    ) -> ForecastResult | None: ...


@runtime_checkable
class Optimizer(Protocol):
    async def rank(
        self, *, problem: dict[str, Any], options: list[dict[str, Any]]
    ) -> RankedInterventions: ...


@runtime_checkable
class StatsEngine(Protocol):
    async def check(self, *, name: str, data: dict[str, Any]) -> StatResult: ...


@dataclass
class ProviderBundle:
    """Everything the swarm needs to run. Swap fields for real implementations."""

    data: dict[str, DataProvider]  # domain -> provider
    anomaly: AnomalyDetector
    causal: CausalEngine
    forecaster: Forecaster
    optimizer: Optimizer
    stats: StatsEngine

    def data_for(self, domain: str) -> DataProvider | None:
        return self.data.get(domain)

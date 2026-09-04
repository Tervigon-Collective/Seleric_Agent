"""Typed contracts for the Prediction subsystem.

The Prediction Agent answers "what happens next if this continues?" as a forecast
*orchestration* agent, not a forecasting LLM. It emits a ``ForecastArtifact`` the
Skeptic already validates (model + forecast validators) plus forecast ``Claim[]``.

Numbers come only from a registered model or an approved statistical baseline;
never from an LLM (``allow_llm_numeric_fallback: false``). When neither is
available the result is ``INSUFFICIENT_PREDICTIVE_EVIDENCE`` with no claim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# Re-export the Skeptic-facing output contracts.
from seleric_swarm.agents.skeptic.contracts import (  # noqa: F401
    Claim,
    ForecastArtifact,
    PredictionArtifact,
)

ForecastSource = Literal["registered_model", "statistical_baseline", "insufficient"]

PredictiveConfidence = Literal[
    "INSUFFICIENT_PREDICTIVE_EVIDENCE",
    "WEAK",
    "MODERATE",
    "STRONG",
]

ApplicabilityStatus = Literal[
    "in_domain",
    "near_domain",
    "out_of_domain",
    "regime_shift",
    "unknown",
]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _rid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #


class PredictionRequest(BaseModel):
    mission_id: str
    question: str = ""
    target_metric: str = ""
    horizon: str = ""                       # e.g. "7d" - resolved from policy if blank
    evidence_refs: list[str] = Field(default_factory=list)
    anomaly_refs: list[str] = Field(default_factory=list)
    diagnostic_refs: list[str] = Field(default_factory=list)
    causal_refs: list[str] = Field(default_factory=list)
    lead_domain: str | None = None
    time_range: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    # optional history series for a real statistical baseline: list[float] most-recent-last
    history: list[float] | None = None
    # optional pandas DataFrame for a model service that fits
    observations: Any = None

    model_config = {"arbitrary_types_allowed": True}


# --------------------------------------------------------------------------- #
# Internal models
# --------------------------------------------------------------------------- #


class ScenarioProjection(BaseModel):
    name: Literal["base", "optimistic", "pessimistic"]
    prediction: float
    interval: list[float] = Field(default_factory=list)
    assumption: str = ""


class ForecastRun(BaseModel):
    """Raw output of the model service / baseline before it becomes an artifact."""

    source: ForecastSource
    target_metric: str
    horizon: str
    prediction: float | None = None
    interval: list[float] = Field(default_factory=list)
    model_id: str | None = None
    model_version: str | None = None
    feature_set_id: str | None = None
    feature_set_version: str | None = None
    training_window: dict[str, Any] = Field(default_factory=dict)
    backtest_metrics: dict[str, Any] = Field(default_factory=dict)
    drift_status: str | None = None
    method: str = ""
    llm_generated: bool = False
    synthetic: bool = False
    notes: list[str] = Field(default_factory=list)


class PredictionResult(BaseModel):
    prediction_run_id: str = Field(default_factory=lambda: _rid("PREDRUN"))
    mission_id: str
    target_metric: str
    horizon: str

    source: ForecastSource
    applicability: ApplicabilityStatus = "unknown"
    confidence: PredictiveConfidence = "INSUFFICIENT_PREDICTIVE_EVIDENCE"

    forecast_artifact: ForecastArtifact | None = None
    scenarios: list[ScenarioProjection] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)

    methodology: str = ""
    limitations: list[str] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)
    synthetic: bool = False
    created_at: str = Field(default_factory=_now)

    def has_forecast(self) -> bool:
        return self.forecast_artifact is not None and self.source != "insufficient"

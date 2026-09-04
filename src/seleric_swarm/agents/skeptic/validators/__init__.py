"""Skeptic validators. Each is small, independently testable, LLM-free."""

from __future__ import annotations

from seleric_swarm.agents.skeptic.validators.anomaly_validator import AnomalyValidator
from seleric_swarm.agents.skeptic.validators.base import Validator
from seleric_swarm.agents.skeptic.validators.causal_validator import CausalValidator
from seleric_swarm.agents.skeptic.validators.contradiction_validator import ContradictionValidator
from seleric_swarm.agents.skeptic.validators.evidence_validator import EvidenceValidator
from seleric_swarm.agents.skeptic.validators.forecast_validator import ForecastValidator
from seleric_swarm.agents.skeptic.validators.metric_validator import MetricValidator
from seleric_swarm.agents.skeptic.validators.model_validator import ModelValidator
from seleric_swarm.agents.skeptic.validators.provenance_validator import ProvenanceValidator
from seleric_swarm.agents.skeptic.validators.statistics_validator import StatisticsValidator
from seleric_swarm.agents.skeptic.validators.strategy_validator import StrategyValidator

CORE_VALIDATORS: list[type[Validator]] = [
    EvidenceValidator,
    ProvenanceValidator,
    MetricValidator,
    ContradictionValidator,
]

TYPE_VALIDATORS: dict[str, type[Validator]] = {
    "statistical": StatisticsValidator,
    "anomaly": AnomalyValidator,
    "causal": CausalValidator,
    "model": ModelValidator,
    "forecast": ForecastValidator,
    "strategy": StrategyValidator,
}

__all__ = [
    "CORE_VALIDATORS",
    "TYPE_VALIDATORS",
    "AnomalyValidator",
    "CausalValidator",
    "ContradictionValidator",
    "EvidenceValidator",
    "ForecastValidator",
    "MetricValidator",
    "ModelValidator",
    "ProvenanceValidator",
    "StatisticsValidator",
    "StrategyValidator",
    "Validator",
]

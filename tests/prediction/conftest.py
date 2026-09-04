"""Fixtures for the Prediction test-suite. Deterministic, offline, no LLM."""

from __future__ import annotations

from typing import Any

import pytest

from seleric_swarm.agents.prediction import PredictionAgent, PredictionDeps
from seleric_swarm.agents.prediction.policies import PredictionPolicies
from seleric_swarm.agents.prediction.registries import (
    FeatureSetRef,
    InMemoryArtifactRepository,
    InMemoryEvidenceRepository,
    InMemoryFeatureStore,
    InMemoryModelRegistry,
    ModelRecord,
    TemplateForecastModelService,
)

MISSION = "MS-PRED"

CAC_FORECAST_TRUTH: dict[str, Any] = {
    "target": "metric.cac",
    "horizon": "7d",
    "model": {
        "id": "forecast.cac.v1",
        "version": "1",
        "feature_set": "features.cac.v1",
        "drift_status": "green",
        "backtest_metric": "MAPE=0.08",
        "mape": 0.08,
    },
    "prediction": 815.0,
    "interval": [770.0, 860.0],
}


def ev(eid: str, metric: str, value: Any, *, change_pct: float | None = None, synthetic: bool = False) -> dict[str, Any]:
    return {
        "evidence_id": eid,
        "mission_id": MISSION,
        "metric_id": metric,
        "value": value,
        "change_pct": change_pct,
        "source": "seleric.metrics_query",
        "synthetic": synthetic,
        "time_range": {"start": "2026-08-31", "end": "2026-09-02"},
    }


def causal(passed: bool = True) -> dict[str, Any]:
    return {
        "artifact_id": "CAUS-1",
        "artifact_type": "causal",
        "mission_id": MISSION,
        "treatment": "metric.mobile_lcp_seconds",
        "outcome": "metric.purchase_cvr",
        "passed": passed,
    }


@pytest.fixture
def policies() -> PredictionPolicies:
    return PredictionPolicies.load()


@pytest.fixture
def approved_model_deps(policies):
    def _factory(
        *,
        evidence: list[dict] | None = None,
        artifacts: list[dict] | None = None,
        truth: dict | None = None,
        model_status: str = "approved",
        last_validated_at: str = "2026-08-20T00:00:00+00:00",
        with_feature_set: bool = True,
        history: list[float] | None = None,
    ) -> tuple[PredictionAgent, dict]:
        t = truth if truth is not None else CAC_FORECAST_TRUTH
        reg = InMemoryModelRegistry()
        reg.add(
            ModelRecord(
                model_id="forecast.cac.v1", version="1", status=model_status,
                target="metric.cac", backtest_available=True, last_validated_at=last_validated_at,
            )
        )
        fs = InMemoryFeatureStore()
        if with_feature_set:
            fs.add("forecast.cac.v1", FeatureSetRef("features.cac.v1", "1"))
        deps = PredictionDeps(
            evidence_repo=InMemoryEvidenceRepository(evidence or [ev("EV-cac", "metric.cac", 782.0, change_pct=29.5)]),
            artifact_repo=InMemoryArtifactRepository(artifacts or [causal()]),
            model_registry=reg,
            feature_store=fs,
            model_service=TemplateForecastModelService(t),
        )
        return PredictionAgent(deps=deps, policies=policies), {"history": history}

    return _factory


@pytest.fixture
def no_model_deps(policies):
    def _factory(evidence: list[dict] | None = None, artifacts: list[dict] | None = None) -> PredictionAgent:
        deps = PredictionDeps(
            evidence_repo=InMemoryEvidenceRepository(evidence or []),
            artifact_repo=InMemoryArtifactRepository(artifacts or []),
            model_registry=InMemoryModelRegistry(),
            model_service=TemplateForecastModelService({}),
        )
        return PredictionAgent(deps=deps, policies=policies)

    return _factory

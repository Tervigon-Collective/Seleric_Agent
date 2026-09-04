"""Fixtures + builders for the Skeptic test-suite.

All fixtures are deterministic: no network, no LLM (``SkepticDeps`` defaults use
the in-memory registries + ``BasicCausalValidationService`` +
``DeterministicStatsValidator``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from seleric_swarm.agents.skeptic import SkepticAgent, SkepticDeps
from seleric_swarm.agents.skeptic.policies import SkepticPolicies
from seleric_swarm.agents.skeptic.registries import (
    CausalGraph,
    InMemoryArtifactRepository,
    InMemoryBusinessRuleService,
    InMemoryEvidenceRepository,
    InMemoryModelRegistry,
    ModelRecord,
    causal_graphs_from_yaml,
    metric_registry_from_yaml,
)

MISSION = "MS-TEST"
NOW = datetime.now(UTC).replace(microsecond=0)


def iso(hours_ago: float = 0.0) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def evidence(
    eid: str,
    metric_id: str,
    value: Any,
    *,
    source: str = "seleric.metrics_query",
    change_pct: float | None = None,
    calc_version: str = "v1",
    dims: dict | None = None,
    sample_size: int | None = 500,
    quality_flags: list[str] | None = None,
    freshness_hours: float = 1.0,
    start: str = "2026-08-31",
    end: str = "2026-09-02",
) -> dict[str, Any]:
    return {
        "evidence_id": eid,
        "mission_id": MISSION,
        "source": source,
        "metric_id": metric_id,
        "value": value,
        "change_pct": change_pct,
        "calculation_version": calc_version,
        "query_hash": f"qh-{eid}",
        "source_version": "sv-1",
        "retrieved_at": iso(freshness_hours),
        "freshness": iso(freshness_hours),
        "time_range": {"start": start, "end": end, "timezone": "Asia/Kolkata"},
        "dimensions": dims or {},
        "sample_size": sample_size,
        "quality_flags": quality_flags or [],
    }


def causal_artifact(
    *,
    aid: str = "CAUS-1",
    treatment: str = "metric.mobile_lcp_seconds",
    outcome: str = "metric.purchase_cvr",
    graph_id: str = "causal.funnel_purchase.v1",
    passed: bool = True,
    refutations: list[dict] | None = None,
    common_causes: list[str] | None = None,
    treatment_started_at: str | None = "2026-09-01T11:47:00+05:30",
    outcome_started_at: str | None = "2026-09-01T12:05:00+05:30",
    estimator: str = "backdoor.linear_regression",
) -> dict[str, Any]:
    return {
        "artifact_id": aid,
        "artifact_type": "causal",
        "mission_id": MISSION,
        "treatment": treatment,
        "outcome": outcome,
        "graph_id": graph_id,
        "common_causes": common_causes or ["metric.sessions", "campaign", "device", "metric.return_rate"],
        "estimator": estimator,
        "effect": -0.62,
        "effect_ci": [-0.81, -0.44],
        "refutations": refutations
        if refutations is not None
        else [
            {"name": "random_common_cause", "passed": True},
            {"name": "placebo_treatment", "passed": True},
            {"name": "data_subset", "passed": True},
        ],
        "passed": passed,
        "treatment_started_at": treatment_started_at,
        "outcome_started_at": outcome_started_at,
        "sample_size": 4200,
    }


def forecast_artifact(
    *,
    aid: str = "PRED-1",
    target: str = "metric.cac",
    model_id: str | None = "forecast.cac.v1",
    model_version: str | None = "1",
    drift_status: str | None = "green",
    interval: list[float] | None = None,
    backtest: dict | None = None,
    llm_generated: bool = False,
) -> dict[str, Any]:
    return {
        "artifact_id": aid,
        "artifact_type": "prediction",
        "mission_id": MISSION,
        "target": target,
        "horizon": "7d",
        "prediction": 815.0,
        "interval": interval if interval is not None else [770.0, 860.0],
        "model": {"id": model_id, "version": model_version, "backtest_metric": "MAPE=0.08" if backtest is None else None},
        "drift_status": drift_status,
        "backtest_metrics": backtest or {},
        "llm_generated": llm_generated,
    }


def strategy_artifact(
    *,
    aid: str = "STRAT-1",
    action: str = "Roll back DEP-4471",
    mechanism_fit: str = "very_high",
    reversibility: str = "high",
    owner_domain: str | None = "technical",
) -> dict[str, Any]:
    return {
        "artifact_id": aid,
        "artifact_type": "strategy",
        "mission_id": MISSION,
        "options": [
            {"action": action, "mechanism_fit": mechanism_fit, "expected_impact": "high", "cost": "low", "risk": "low", "reversibility": reversibility},
        ],
        "recommended": [action],
        "owner_domain": owner_domain,
        "evidence_refs": ["CAUS-1"],
    }


def anomaly_artifact(
    *,
    aid: str = "AN-1",
    metric_id: str = "metric.purchase_cvr",
    deviation_pct: float = -24.0,
    detector_id: str | None = "stl.v1",
    detector_version: str | None = "1.0",
    history_days: int | None = 90,
    sample_size: int | None = 5000,
) -> dict[str, Any]:
    return {
        "artifact_id": aid,
        "artifact_type": "anomaly",
        "mission_id": MISSION,
        "metric_id": metric_id,
        "observed": 2.35,
        "expected_range": [2.95, 3.25],
        "deviation_pct": deviation_pct,
        "score": 0.8,
        "detector": {"id": detector_id, "version": detector_version, "history_days": history_days, "sample_size": sample_size},
        "evidence_refs": ["EV-cvr"],
    }


@pytest.fixture
def policies() -> SkepticPolicies:
    return SkepticPolicies.load()


@pytest.fixture
def deps() -> SkepticDeps:
    graphs = causal_graphs_from_yaml()
    if graphs.get("causal.funnel_purchase.v1") is None:
        graphs.add(
            CausalGraph(
                "causal.funnel_purchase.v1",
                nodes=["page_latency", "add_to_cart", "purchase"],
                edges=[("page_latency", "add_to_cart"), ("add_to_cart", "purchase")],
            )
        )
    models = InMemoryModelRegistry()
    models.add(
        ModelRecord(
            model_id="forecast.cac.v1",
            version="1",
            status="approved",
            target="metric.cac",
            minimum_history_days=56,
            backtest_available=True,
            last_validated_at=iso(24 * 10),
        )
    )
    return SkepticDeps(
        metric_registry=metric_registry_from_yaml(),
        model_registry=models,
        causal_graphs=graphs,
        rules=InMemoryBusinessRuleService(),
    )


@pytest.fixture
def make_agent(deps: SkepticDeps, policies: SkepticPolicies):
    def _factory(evidence_rows: list[dict] | None = None, artifact_rows: list[dict] | None = None,
                 *, rule_context: dict | None = None) -> SkepticAgent:
        d = deps
        if rule_context is not None:
            d = SkepticDeps(
                metric_registry=deps.metric_registry,
                model_registry=deps.model_registry,
                causal_graphs=deps.causal_graphs,
                incident_registry=deps.incident_registry,
                rules=InMemoryBusinessRuleService(rule_context),
                stats=deps.stats,
            )
        return SkepticAgent(
            evidence_repo=InMemoryEvidenceRepository(evidence_rows or []),
            artifact_repo=InMemoryArtifactRepository(artifact_rows or []),
            deps=d,
            policies=policies,
        )

    return _factory

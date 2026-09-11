"""Fixtures for the Diagnostic test-suite. Deterministic, offline, no LLM.

This module is a harness (row builders + an in-memory agent factory). It does
not encode a product RCA story: no default treatment/outcome pair, no canned
causal truth, no domain labels that are not on the evidence row itself.
"""

from __future__ import annotations

from typing import Any

import pytest

from seleric_swarm.agents.diagnostic import DiagnosticAgent, DiagnosticDeps
from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies
from seleric_swarm.agents.diagnostic.registries import (
    InMemoryAnomalyRepository,
    InMemoryArtifactRepository,
    InMemoryCausalGraphRegistry,
    InMemoryEvidenceRepository,
    TemplateCausalEstimationService,
    causal_graphs_from_yaml,
)

MISSION = "MS-DIAG"


def ev(
    eid: str,
    metric: str,
    value: Any,
    *,
    change_pct: float | None = None,
    dims: dict | None = None,
    start: str = "2026-09-01",
    start_time: str | None = None,
    source: str = "seleric.metrics_query",
    is_event: bool = False,
    provenance: dict | None = None,
    sample_size: int | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "evidence_id": eid,
        "mission_id": MISSION,
        "source": source,
        "value": value,
        "change_pct": change_pct,
        "dimensions": dims or {},
        "time_range": {"start": start, "end": "2026-09-02", "timezone": "Asia/Kolkata"},
    }
    if start_time:
        row["start_time"] = start_time
    if is_event:
        row["metric_or_fact"] = metric
    else:
        row["metric_id"] = metric
    if provenance:
        row["provenance"] = provenance
    if sample_size is not None:
        row["sample_size"] = sample_size
    return row


def anomaly(
    aid: str,
    metric: str,
    deviation: float,
    *,
    direction: str = "down",
    start_time: str | None = None,
) -> dict[str, Any]:
    return {
        "anomaly_id": aid,
        "mission_id": MISSION,
        "metric_id": metric,
        "deviation_pct": deviation,
        "direction": direction,
        "score": 0.8,
        "start_time": start_time,
    }


def matching_truth(treatment: str, outcome: str, **overrides: Any) -> dict[str, Any]:
    """Offline estimator answer for one (treatment, outcome) pair.

    Tests that need a retained causal finding pass this explicitly. The agent
    factory does not inject a default pair.
    """
    row: dict[str, Any] = {
        "graph_id": "causal.funnel_purchase.v1",
        "treatment": treatment,
        "outcome": outcome,
        "common_causes": ["metric.sessions", "campaign", "device"],
        "effect": -0.62,
        "effect_ci": [-0.81, -0.44],
        "refutations": [
            {"name": "placebo_treatment", "passed": True},
            {"name": "random_common_cause", "passed": True},
            {"name": "data_subset", "passed": True},
        ],
        "passed": True,
    }
    row.update(overrides)
    return row


@pytest.fixture
def policies() -> DiagnosticPolicies:
    return DiagnosticPolicies.load()


@pytest.fixture
def graphs() -> InMemoryCausalGraphRegistry:
    return causal_graphs_from_yaml()


@pytest.fixture
def make_agent(policies: DiagnosticPolicies, graphs: InMemoryCausalGraphRegistry):
    def _factory(
        evidence: list[dict] | None = None,
        anomalies: list[dict] | None = None,
        *,
        causal_truth: dict | None = None,
        artifacts: list[dict] | None = None,
    ) -> DiagnosticAgent:
        deps = DiagnosticDeps(
            evidence_repo=InMemoryEvidenceRepository(evidence or []),
            artifact_repo=InMemoryArtifactRepository(artifacts or []),
            anomaly_repo=InMemoryAnomalyRepository(anomalies or []),
            causal_graphs=graphs,
            causal_service=TemplateCausalEstimationService(causal_truth or {}),
        )
        return DiagnosticAgent(deps=deps, policies=policies)

    return _factory

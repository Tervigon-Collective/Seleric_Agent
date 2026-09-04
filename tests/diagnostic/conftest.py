"""Fixtures for the Diagnostic test-suite. Deterministic, offline, no LLM."""

from __future__ import annotations

from typing import Any

import pytest

from seleric_swarm.agents.diagnostic import DiagnosticAgent, DiagnosticDeps
from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies
from seleric_swarm.agents.diagnostic.registries import (
    CausalGraph,
    InMemoryAnomalyRepository,
    InMemoryArtifactRepository,
    InMemoryCausalGraphRegistry,
    InMemoryEvidenceRepository,
    TemplateCausalEstimationService,
    causal_graphs_from_yaml,
)

MISSION = "MS-DIAG"

CAC_TRUTH = {
    "graph_id": "causal.funnel_purchase.v1",
    "treatment": "metric.mobile_lcp_seconds",
    "outcome": "metric.purchase_cvr",
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
    return row


def anomaly(aid: str, metric: str, deviation: float, *, direction: str = "down", start_time: str | None = None) -> dict[str, Any]:
    return {
        "anomaly_id": aid,
        "mission_id": MISSION,
        "metric_id": metric,
        "deviation_pct": deviation,
        "direction": direction,
        "score": 0.8,
        "start_time": start_time,
    }


@pytest.fixture
def policies() -> DiagnosticPolicies:
    return DiagnosticPolicies.load()


@pytest.fixture
def graphs() -> InMemoryCausalGraphRegistry:
    reg = causal_graphs_from_yaml()
    if reg.get("causal.funnel_purchase.v1") is None:
        reg.add(
            CausalGraph(
                "causal.funnel_purchase.v1",
                nodes=["page_latency", "add_to_cart", "purchase", "price", "stock", "payment_failure"],
                edges=[("page_latency", "add_to_cart"), ("add_to_cart", "purchase"),
                       ("price", "add_to_cart"), ("stock", "purchase"), ("payment_failure", "purchase")],
            )
        )
    return reg


@pytest.fixture
def make_agent(policies: DiagnosticPolicies, graphs: InMemoryCausalGraphRegistry):
    def _factory(
        evidence: list[dict] | None = None,
        anomalies: list[dict] | None = None,
        *,
        causal_truth: dict | None = CAC_TRUTH,
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


# a reusable "mobile latency regression" evidence bundle
def latency_bundle() -> list[dict[str, Any]]:
    return [
        ev("EV-cvr", "metric.purchase_cvr", 2.35, change_pct=-24.0),
        ev("EV-mcvr", "metric.purchase_cvr", 2.03, change_pct=-31.0, dims={"device": "mobile"}),
        ev("EV-dcvr", "metric.purchase_cvr", 3.30, change_pct=-3.0, dims={"device": "desktop", "segment": "control"}),
        ev("EV-lcp", "metric.mobile_lcp_seconds", 5.8, change_pct=164.0, start_time="2026-09-01T11:47:00+05:30"),
        ev("EV-js", "metric.js_error_rate", 6.1, change_pct=771.0, start_time="2026-09-01T11:47:00+05:30"),
        ev("EV-dep", "event.frontend_deployment", "2026-09-01T11:40:00+05:30", is_event=True),
    ]

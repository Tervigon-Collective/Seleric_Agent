"""Mission decomposition into a task DAG (pasted spec sec. 9-11).

This produces the *plan representation* the coordinator attaches to mission
state: real ``Task`` objects with capabilities, metric reads, dependencies and a
dispatchability verdict. It does not rewrite the LangGraph edges - execution
still flows through orchestration/graph.py - but every downstream plane
(gap detection, completion scoring, the unsupported-reason writer) reads it.
"""

from __future__ import annotations

from collections.abc import Sequence

from seleric_swarm.coordinator.models import ComplexityLevel, Task, TaskGraph
from seleric_swarm.coordinator.routing.dispatchability import DispatchGuard

_COMMERCE_METRICS = {"metric.net_sales", "metric.gross_sales"}
_PERFORMANCE_METRICS = {"metric.cac"}

_DEFAULT_METRIC = {
    "commerce_agent": "metric.net_sales",
    "performance_agent": "metric.cac",
}

# Capabilities the upper analytical bands would require. None are wired in V1, so
# tasks carrying them are emitted only to explain *why* a request is unsupported.
_BAND_CAPABILITIES: dict[ComplexityLevel, list[tuple[str, str, str]]] = {
    ComplexityLevel.L3: [("anomaly_analysis", "detect_anomaly", "Detect whether the change is statistically anomalous")],
    ComplexityLevel.L4: [
        ("anomaly_analysis", "detect_anomaly", "Detect whether the change is statistically anomalous"),
        ("hypothesis_generation", "generate_hypotheses", "Generate candidate explanations"),
        ("causal_diagnosis", "causal_validation", "Validate the most plausible cause"),
    ],
    ComplexityLevel.L5: [
        ("anomaly_analysis", "detect_anomaly", "Detect whether the change is statistically anomalous"),
        ("hypothesis_generation", "generate_hypotheses", "Generate candidate explanations"),
        ("causal_diagnosis", "causal_validation", "Validate the most plausible cause"),
        ("forecasting", "forecast_impact", "Forecast the impact if the trend continues"),
        ("intervention_design", "recommend_actions", "Recommend interventions"),
        ("challenge", "skeptic_review", "Challenge the conclusion before it ships"),
    ],
}


def _resolve_metrics(mission_lead: str, metric_hints: Sequence[str]) -> list[str]:
    hints = [m for m in metric_hints if m.startswith("metric.")]
    if hints:
        return list(dict.fromkeys(hints))
    default = _DEFAULT_METRIC.get(mission_lead)
    return [default] if default else []


def build_task_dag(
    *,
    query_class: str,
    mission_lead: str,
    complexity: ComplexityLevel,
    metric_hints: Sequence[str] | None = None,
    guard: DispatchGuard | None = None,
) -> TaskGraph:
    metric_hints = list(metric_hints or [])
    metrics = _resolve_metrics(mission_lead, metric_hints)
    tasks: list[Task] = []
    notes: list[str] = []

    commerce_metrics = [m for m in metrics if m in _COMMERCE_METRICS]
    performance_metrics = [m for m in metrics if m in _PERFORMANCE_METRICS]

    if query_class in {"lookup", "comparison"}:
        primary_metrics = performance_metrics or commerce_metrics or metrics
        observe = Task(
            id="T1",
            type="observe_metric",
            objective=f"Retrieve {', '.join(primary_metrics) or 'the requested metric'} for the resolved time range",
            required_capabilities=["metric_observation", "evidence_collection"],
            metric_ids=list(primary_metrics),
            expected_artifacts=["evidence_bundle"],
            assigned_agent="observer_agent",
            priority=9,
        )
        tasks.append(observe)

        # Cross-domain lookup: performance lead, but commerce metrics still owed.
        if performance_metrics and commerce_metrics:
            tasks.append(
                Task(
                    id="T2",
                    type="observe_metric",
                    objective=f"Retrieve {', '.join(commerce_metrics)} after leadership transfers to commerce",
                    required_capabilities=["metric_observation", "evidence_collection"],
                    metric_ids=list(commerce_metrics),
                    depends_on=["T1"],
                    expected_artifacts=["evidence_bundle"],
                    assigned_agent="observer_agent",
                    priority=8,
                )
            )
            notes.append("Cross-domain lookup: expect one leadership transfer performance -> commerce.")

        observe_ids = [t.id for t in tasks]
        tasks.append(
            Task(
                id="T-gate",
                type="claim_gate",
                objective="Gate every numeric claim on provenance before synthesis",
                required_capabilities=[],
                depends_on=observe_ids,
                expected_artifacts=["gated_claims"],
                assigned_agent="claim_gate",
                dispatchable=True,
                priority=6,
            )
        )
        tasks.append(
            Task(
                id="T-synth",
                type="synthesize",
                objective="Write the business answer using only gated claims",
                required_capabilities=["synthesis"],
                depends_on=["T-gate"],
                expected_artifacts=["final_response"],
                assigned_agent="coordinator_agent",
                dispatchable=True,
                priority=5,
            )
        )
    else:
        notes.append(
            f"query_class={query_class!r}: V1 supports only lookup/comparison retrieval. "
            "The tasks below are the shape this question would require."
        )
        band = _BAND_CAPABILITIES.get(complexity, [])
        prev = None
        # A hypothetical observation still anchors the investigation.
        anchor = Task(
            id="T1",
            type="observe_metric",
            objective="Establish the baseline for the metric in question",
            required_capabilities=["metric_observation", "evidence_collection"],
            metric_ids=list(metrics),
            assigned_agent="observer_agent",
            priority=9,
        )
        tasks.append(anchor)
        prev = "T1"
        for idx, (cap, ttype, objective) in enumerate(band, start=2):
            tid = f"T{idx}"
            tasks.append(
                Task(
                    id=tid,
                    type=ttype,
                    objective=objective,
                    required_capabilities=[cap],
                    depends_on=[prev] if prev else [],
                    priority=max(1, 9 - idx),
                )
            )
            prev = tid

    graph = TaskGraph(tasks=tasks, complexity=complexity, notes=notes)
    if guard is not None:
        for task in graph.tasks:
            if task.assigned_agent in {"claim_gate", "coordinator_agent"} and not task.required_capabilities:
                task.dispatchable = True
                continue
            guard.annotate(task)
    return graph

"""Unit tests for the deterministic coordinator control plane."""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator import ControlPlane
from seleric_swarm.coordinator.governance.budget import (
    MissionLimits,
    check_budget,
    check_hard_stops,
)
from seleric_swarm.coordinator.governance.completion import assess_completion
from seleric_swarm.coordinator.leadership.lead_selector import select_initial_lead
from seleric_swarm.coordinator.models import ComplexityLevel, Task, TaskGraph
from seleric_swarm.coordinator.planning.complexity import classify_complexity
from seleric_swarm.coordinator.planning.dag_builder import build_task_dag
from seleric_swarm.coordinator.routing.agent_selector import score_agent, select_agent
from seleric_swarm.coordinator.routing.capability_resolver import CapabilityResolver
from seleric_swarm.coordinator.routing.dispatchability import DispatchGuard

# --------------------------------------------------------------------------- #
# complexity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query_class,query,hints,entities,expected",
    [
        ("lookup", "net sales on 2026-08-01", [], [], ComplexityLevel.L0),
        ("lookup", "net sales for the India store", ["metric.net_sales"], ["store:india"], ComplexityLevel.L1),
        ("lookup", "cac and net sales", ["metric.cac", "metric.net_sales"], [], ComplexityLevel.L1),
        ("comparison", "compare net sales", [], [], ComplexityLevel.L2),
        ("unsupported", "why did CAC increase?", ["metric.cac"], [], ComplexityLevel.L4),
        ("unsupported", "why did CAC increase and what should we do?", ["metric.cac", "metric.net_sales"], [], ComplexityLevel.L5),
        ("unsupported", "forecast next week sales", [], [], ComplexityLevel.L3),
    ],
)
def test_classify_complexity(query_class, query, hints, entities, expected):
    assert classify_complexity(
        query_class=query_class, query=query, metric_hints=hints, entities=entities
    ) == expected


# --------------------------------------------------------------------------- #
# capability resolver + dispatchability
# --------------------------------------------------------------------------- #


def test_capability_resolver_flags_wired_agents(runtime):
    resolver = CapabilityResolver(runtime.agents)
    res = resolver.resolve("metric_observation")
    assert "observer_agent" in res.candidates
    assert res.wired_candidates == ["observer_agent"]

    stub = resolver.resolve("causal_diagnosis")
    assert "diagnostic_agent" in stub.candidates
    assert stub.wired_candidates == []
    assert stub.resolvable is False


def test_dispatch_guard_blocks_missing_data_path(runtime):
    live_guard = DispatchGuard(
        CapabilityResolver(runtime.agents), runtime.metrics, {"seleric.metrics_query"}
    )
    live = Task(
        id="T1",
        type="observe_metric",
        objective="net sales",
        required_capabilities=["metric_observation"],
        metric_ids=["metric.net_sales"],
    )
    assert live_guard.check(live).dispatchable is True

    unregistered = Task(
        id="T1b",
        type="observe_metric",
        objective="made up metric",
        required_capabilities=["metric_observation"],
        metric_ids=["metric.does_not_exist"],
    )
    verdict = live_guard.check(unregistered)
    assert verdict.dispatchable is False
    assert "not in the metric registry" in verdict.reason

    # metric resolution is dynamic (catalogue_search_metrics at observe time),
    # so what actually gates dispatch is whether the live gateway is up at all.
    offline_guard = DispatchGuard(CapabilityResolver(runtime.agents), runtime.metrics, set())
    no_data = Task(
        id="T2",
        type="observe_metric",
        objective="net sales",
        required_capabilities=["metric_observation"],
        metric_ids=["metric.net_sales"],
    )
    verdict = offline_guard.check(no_data)
    assert verdict.dispatchable is False
    assert "No live MCP data path" in verdict.reason

    stub_cap = Task(
        id="T3", type="causal_validation", objective="x", required_capabilities=["causal_diagnosis"]
    )
    assert live_guard.check(stub_cap).dispatchable is False


# --------------------------------------------------------------------------- #
# dag builder
# --------------------------------------------------------------------------- #


def test_build_task_dag_lookup_is_fully_dispatchable(runtime):
    guard = DispatchGuard(
        CapabilityResolver(runtime.agents), runtime.metrics, set(runtime.mcp.capabilities)
    )
    graph = build_task_dag(
        query_class="lookup",
        mission_lead="commerce_agent",
        complexity=ComplexityLevel.L0,
        metrics=runtime.metrics,
        metric_hints=["metric.net_sales"],
        guard=guard,
    )
    assert graph.dispatchable is True
    assert [t.id for t in graph.tasks] == ["T1", "T-gate", "T-synth"]
    assert graph.ready()[0].id == "T1"


def test_build_task_dag_cross_domain_adds_transfer_task(runtime):
    guard = DispatchGuard(
        CapabilityResolver(runtime.agents), runtime.metrics, set(runtime.mcp.capabilities)
    )
    graph = build_task_dag(
        query_class="lookup",
        mission_lead="performance_agent",
        complexity=ComplexityLevel.L1,
        metrics=runtime.metrics,
        metric_hints=["metric.cac", "metric.net_sales"],
        guard=guard,
    )
    t2 = graph.get("T2")
    assert t2 is not None
    assert t2.metric_ids == ["metric.net_sales"]
    assert t2.depends_on == ["T1"]
    assert graph.dispatchable is True


def test_build_task_dag_unsupported_is_blocked_with_reasons(runtime):
    guard = DispatchGuard(
        CapabilityResolver(runtime.agents), runtime.metrics, set(runtime.mcp.capabilities)
    )
    graph = build_task_dag(
        query_class="unsupported",
        mission_lead="performance_agent",
        complexity=ComplexityLevel.L5,
        metrics=runtime.metrics,
        metric_hints=["metric.cac"],
        guard=guard,
    )
    assert graph.dispatchable is False
    blocked = {t.type for t in graph.blocked_tasks}
    assert {"generate_hypotheses", "causal_validation", "skeptic_review"} & blocked


def test_task_graph_roundtrips_through_dict():
    graph = TaskGraph(tasks=[Task(id="T1", type="observe", objective="x")], complexity=ComplexityLevel.L2)
    restored = TaskGraph.from_dict(graph.to_dict())
    assert restored.complexity == ComplexityLevel.L2
    assert restored.tasks[0].id == "T1"


# --------------------------------------------------------------------------- #
# lead selector
# --------------------------------------------------------------------------- #


def test_select_initial_lead_keeps_valid_llm_lead(runtime):
    decision = select_initial_lead(
        llm_domain_lead="performance_agent",
        metric_hints=["metric.cac"],
        metrics=runtime.metrics,
        agents=runtime.agents,
    )
    assert decision.mission_lead == "performance_agent"
    assert decision.source == "llm"


def test_select_initial_lead_falls_back_to_metric_ownership(runtime):
    decision = select_initial_lead(
        llm_domain_lead=None,
        metric_hints=["metric.net_sales"],
        metrics=runtime.metrics,
        agents=runtime.agents,
    )
    assert decision.mission_lead == "commerce_agent"
    assert decision.source == "metric_ownership"


# --------------------------------------------------------------------------- #
# agent selector scoring
# --------------------------------------------------------------------------- #


def test_score_agent_prefers_capability_match():
    strong = {"id": "a", "capabilities": ["funnel_analysis", "device_analysis"]}
    weak = {"id": "b", "capabilities": ["orders"]}
    picked = select_agent(
        [strong, weak], required_capabilities={"funnel_analysis", "device_analysis"}
    )
    assert picked.agent_id == "a"
    assert picked.score > score_agent(weak, required_capabilities={"funnel_analysis"}).score


# --------------------------------------------------------------------------- #
# budget + hard stops
# --------------------------------------------------------------------------- #


def test_check_budget_matches_legacy_semantics():
    limits = MissionLimits(max_llm_calls=6, max_tool_calls=8)
    assert check_budget({"llm_calls": 5}, limits, llm_needed=1).ok is True
    verdict = check_budget({"llm_calls": 6}, limits, llm_needed=1)
    assert verdict.ok is False
    assert verdict.error_code == "BUDGET_EXCEEDED"
    tight = MissionLimits(max_llm_calls=0, max_tool_calls=8)
    assert check_budget({"llm_calls": 0}, tight, llm_needed=1).ok is False


def test_check_hard_stops_trips_on_iterations_and_transfers():
    limits = MissionLimits(max_llm_calls=6, max_tool_calls=8, max_iterations=3, max_leadership_transfers=2)
    assert check_hard_stops({"coordinator_iterations": 3}, limits).ok is True
    assert check_hard_stops({"coordinator_iterations": 4}, limits).ok is False
    assert check_hard_stops({"handoff_history": [1, 2, 3]}, limits).ok is False


# --------------------------------------------------------------------------- #
# completion
# --------------------------------------------------------------------------- #


def test_assess_completion_finishes_clean_numeric_lookup():
    state = {
        "evidence": [{"metric_or_fact": "metric.net_sales", "value": 1}],
        "claims": [{"gate_status": "passed"}],
        "status": "completed",
    }
    tg = {"tasks": [{"id": "T1", "dispatchable": True, "status": "done"}]}
    out = assess_completion(state, tg)
    assert out.decision == "finish"
    assert out.score >= 0.9


def test_assess_completion_finishes_on_advisory_dag(runtime):
    # Plan tasks stay "pending" because the live execution loop is not wired yet;
    # a completed mission with gated claims should still score "finish".
    state = {
        "status": "completed",
        "evidence": [{"metric_or_fact": "metric.net_sales", "value": 1}],
        "claims": [{"gate_status": "passed"}],
    }
    tg = {"tasks": [{"id": "T1", "dispatchable": True, "status": "pending"}]}
    assert assess_completion(state, tg).decision == "finish"


def test_assess_completion_continues_without_evidence():
    out = assess_completion({"evidence": [], "claims": []}, None)
    assert out.decision == "continue"
    assert "Evidence is incomplete or partial for the requested scope" in out.unresolved


def test_assess_completion_partial_mission_does_not_finish():
    state = {
        "status": "partial",
        "evidence": [{"metric_or_fact": "metric.net_sales", "value": 1}],
        "claims": [{"gate_status": "passed"}],
    }
    tg = {"tasks": [{"id": "T1", "dispatchable": True, "status": "pending"}]}
    out = assess_completion(state, tg)
    assert out.decision != "finish"
    assert out.score < 0.9


# --------------------------------------------------------------------------- #
# ControlPlane facade
# --------------------------------------------------------------------------- #


def test_control_plane_plan_for_supported_lookup(runtime):
    plane = ControlPlane(runtime)
    plan = plane.plan(
        query="What were net sales yesterday?",
        query_class="lookup",
        llm_domain_lead="commerce_agent",
        metric_hints=["metric.net_sales"],
        entities=[],
    )
    assert plan["plan_dispatchable"] is True
    assert plan["complexity"] == int(ComplexityLevel.L0)
    assert plan["lead_selection"]["mission_lead"] == "commerce_agent"


def test_control_plane_marks_diagnostic_unsupported_reason(runtime):
    plane = ControlPlane(runtime)
    plan = plane.plan(
        query="Why did CAC increase?",
        query_class="unsupported",
        llm_domain_lead="performance_agent",
        metric_hints=["metric.cac"],
        entities=[],
    )
    assert plan["plan_dispatchable"] is False
    reason = plane.unsupported_reason(plan, "not enabled in V1")
    assert "complexity L4" in reason
    assert "Blocked:" in reason


def test_control_plane_budget_verdict_bridges_to_error_code(runtime):
    runtime.settings.max_llm_calls = 0
    plane = ControlPlane(runtime)  # limits are snapshotted at construction, as in build_graph
    ok, code, _reason = plane.budget_verdict({"llm_calls": 0}, llm_needed=1)
    assert ok is False and code == "BUDGET_EXCEEDED"

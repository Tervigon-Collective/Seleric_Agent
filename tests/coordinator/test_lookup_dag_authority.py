"""Authoritative lookup_v1 TaskGraph routing tests."""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.execution.lookup_dag import (
    dag_progress_summary,
    mark_ready_tasks_done,
    mark_tasks,
    route_from_plan,
)
from seleric_swarm.coordinator.models import ComplexityLevel, Task, TaskGraph
from seleric_swarm.orchestration.runner import run_mission


def _sample_graph(*, dispatchable: bool = True) -> dict:
    g = TaskGraph(
        tasks=[
            Task(
                id="T1",
                type="observe_metric",
                objective="observe",
                dispatchable=dispatchable,
                status="pending",
                assigned_agent="observer_agent",
            ),
            Task(
                id="T-gate",
                type="claim_gate",
                objective="gate",
                dispatchable=True,
                status="pending",
                depends_on=["T1"],
                assigned_agent="claim_gate",
            ),
            Task(
                id="T-synth",
                type="synthesize",
                objective="synth",
                dispatchable=True,
                status="pending",
                depends_on=["T-gate"],
                assigned_agent="coordinator_agent",
            ),
        ],
        complexity=ComplexityLevel.L0,
    )
    return g.to_dict()


def test_route_from_plan_requires_dispatchable():
    state = {
        "query_class": "lookup",
        "mission_lead": "commerce_agent",
        "plan_dispatchable": False,
        "task_graph": _sample_graph(dispatchable=False),
        "lead_selection": {"mission_lead": "commerce_agent"},
    }
    assert (
        route_from_plan(
            state,
            supported_classes={"lookup", "comparison"},
            domain_node_names={"commerce_agent": "domain_commerce_agent"},
        )
        == "finalize_unsupported"
    )


def test_route_from_plan_prefers_live_mission_lead():
    """After handoff, mission_lead must win over stale lead_selection."""
    state = {
        "query_class": "lookup",
        "mission_lead": "commerce_agent",
        "plan_dispatchable": True,
        "task_graph": _sample_graph(),
        "lead_selection": {"mission_lead": "performance_agent"},
    }
    assert (
        route_from_plan(
            state,
            supported_classes={"lookup"},
            domain_node_names={
                "commerce_agent": "domain_commerce_agent",
                "performance_agent": "domain_performance_agent",
            },
        )
        == "domain_commerce_agent"
    )


def test_route_from_plan_falls_back_to_lead_selection():
    state = {
        "query_class": "lookup",
        "mission_lead": None,
        "plan_dispatchable": True,
        "task_graph": _sample_graph(),
        "lead_selection": {"mission_lead": "commerce_agent"},
    }
    assert (
        route_from_plan(
            state,
            supported_classes={"lookup"},
            domain_node_names={"commerce_agent": "domain_commerce_agent"},
        )
        == "domain_commerce_agent"
    )


def test_mark_ready_observe_only_first_wave():
    g = TaskGraph(
        tasks=[
            Task(id="T1", type="observe_metric", objective="a", dispatchable=True, status="pending"),
            Task(
                id="T2",
                type="observe_metric",
                objective="b",
                dispatchable=True,
                status="pending",
                depends_on=["T1"],
            ),
        ]
    )
    state = {"task_graph": g.to_dict()}
    patch = mark_ready_tasks_done(state, task_types={"observe_metric"})
    statuses = {t["id"]: t["status"] for t in patch["task_graph"]["tasks"]}
    assert statuses["T1"] == "done"
    assert statuses["T2"] == "pending"

    # Second wave after T1 done
    patch2 = mark_ready_tasks_done({"task_graph": patch["task_graph"]}, task_types={"observe_metric"})
    statuses2 = {t["id"]: t["status"] for t in patch2["task_graph"]["tasks"]}
    assert statuses2["T2"] == "done"


def test_mark_claim_and_synth():
    state = {"task_graph": _sample_graph()}
    # Pretend observe done
    state = {**state, **mark_tasks(state, task_ids={"T1"}, status="done")}
    state = {**state, **mark_tasks(state, task_types={"claim_gate"}, status="done")}
    state = {**state, **mark_tasks(state, task_types={"synthesize"}, status="done")}
    summary = dag_progress_summary(state)
    assert summary["done"] == 3
    assert summary["pending"] == 0


@pytest.mark.asyncio
async def test_lookup_mission_marks_dag_tasks_done(runtime):
    result = await run_mission(
        runtime,
        query="What were net sales on 2026-08-01?",
        timezone="Asia/Kolkata",
        as_of="2026-08-01",
    )
    assert result.status == "completed"
    state = runtime.store.get_raw(result.mission_id)
    assert state is not None
    tasks = (state.get("task_graph") or {}).get("tasks") or []
    assert tasks, "expected authoritative task_graph on stored mission state"
    by_id = {t["id"]: t["status"] for t in tasks}
    assert by_id.get("T1") == "done"
    assert by_id.get("T-gate") == "done"
    assert by_id.get("T-synth") == "done"
    assert state.get("completion_score") is not None or state.get("completion_decision") is not None

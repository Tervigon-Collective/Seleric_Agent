"""Execution engine + conflict governance unit tests."""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.agent import COORDINATOR_SYSTEM_PROMPT
from seleric_swarm.coordinator.contracts import AgentContext, AgentExecutionResult, TaskSpec
from seleric_swarm.coordinator.execution import (
    ExecutionEngine,
    classify_failure,
    get_ready_tasks,
    should_retry,
)
from seleric_swarm.coordinator.governance.conflicts import detect_conflicts
from seleric_swarm.coordinator.governance.synthetic_guard import mission_synthetic_status


class _FakeInvoker:
    async def invoke(self, agent_id: str, task: TaskSpec, context: AgentContext) -> AgentExecutionResult:
        return AgentExecutionResult(
            agent_id=agent_id,
            task_id=task.task_id,
            status="success",
            artifact_refs=[f"EV-{task.task_id}"],
        )


def test_scheduler_ready_respects_dependencies():
    tasks = [
        TaskSpec(
            task_id="T1",
            mission_id="M",
            task_type="observe",
            objective="a",
            idempotency_key="k1",
            status="pending",
            priority=9,
        ),
        TaskSpec(
            task_id="T2",
            mission_id="M",
            task_type="observe",
            objective="b",
            dependencies=["T1"],
            idempotency_key="k2",
            status="pending",
            priority=8,
        ),
    ]
    ready = get_ready_tasks(tasks)
    assert [t.task_id for t in ready] == ["T1"]


@pytest.mark.asyncio
async def test_execution_engine_wave():
    tasks = [
        TaskSpec(
            task_id="T1",
            mission_id="M",
            task_type="observe",
            objective="a",
            idempotency_key="k1",
            status="ready",
            assigned_agent="observer_agent",
            priority=9,
        )
    ]
    engine = ExecutionEngine(_FakeInvoker())
    updated, results, patch = await engine.run_wave(tasks, {"mission_id": "M", "usage": {}})
    assert results[0].status == "success"
    assert updated[0].status == "done"
    assert patch["agent_calls"] == 1


def test_retry_classification():
    assert classify_failure("timeout") == "retryable_failure"
    assert classify_failure("missing_causal_graph") == "blocking_failure"
    assert should_retry("retryable_failure", attempt=0) is True
    assert should_retry("blocking_failure", attempt=0) is False


def test_detect_data_contradiction():
    conflicts = detect_conflicts(
        {
            "evidence": [
                {
                    "artifact_id": "EV-1",
                    "metric_or_fact": "metric.cac",
                    "value": 100,
                    "dimensions": {},
                    "time_range": {"start": "a", "end": "b"},
                },
                {
                    "artifact_id": "EV-2",
                    "metric_or_fact": "metric.cac",
                    "value": 200,
                    "dimensions": {},
                    "time_range": {"start": "a", "end": "b"},
                },
            ]
        }
    )
    assert any(c["type"] == "DATA_CONTRADICTION" for c in conflicts)


def test_synthetic_status_and_system_prompt():
    assert mission_synthetic_status(all_synthetic=True, mixed=False, complete=True) == "prototype_completed"
    assert mission_synthetic_status(all_synthetic=False, mixed=False, complete=True) == "completed"
    assert "Evidence owns truth" in COORDINATOR_SYSTEM_PROMPT

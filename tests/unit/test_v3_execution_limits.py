from __future__ import annotations

import time

from seleric_swarm.agent.dependencies import ExecutionLimits
from seleric_swarm.agent.limits import ExecutionBudgetTracker


def test_consume_ok_under_limit() -> None:
    tracker = ExecutionBudgetTracker(limits=ExecutionLimits(max_tool_calls=2))
    assert tracker.consume("tool_calls").ok
    assert tracker.tool_calls == 1


def test_consume_rejects_over_limit() -> None:
    tracker = ExecutionBudgetTracker(limits=ExecutionLimits(max_tool_calls=1))
    assert tracker.consume("tool_calls").ok
    verdict = tracker.consume("tool_calls")
    assert not verdict.ok
    assert verdict.error_code == "EXECUTION_LIMIT_EXCEEDED"
    assert verdict.exhausted_key == "tool_calls"
    # A rejected consume must not have mutated the counter.
    assert tracker.tool_calls == 1


def test_consume_unknown_counter_raises() -> None:
    tracker = ExecutionBudgetTracker(limits=ExecutionLimits())
    try:
        tracker.consume("not_a_real_counter")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an unknown counter")


def test_check_runtime_rejects_once_elapsed() -> None:
    tracker = ExecutionBudgetTracker(limits=ExecutionLimits(max_runtime_seconds=0.01))
    time.sleep(0.05)
    verdict = tracker.check_runtime()
    assert not verdict.ok
    assert verdict.exhausted_key == "runtime_seconds"


def test_check_runtime_ok_within_budget() -> None:
    tracker = ExecutionBudgetTracker(limits=ExecutionLimits(max_runtime_seconds=60.0))
    assert tracker.check_runtime().ok

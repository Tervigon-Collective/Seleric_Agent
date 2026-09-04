"""Execution engine — schedule → budget-select → dispatch → merge."""

from __future__ import annotations

from typing import Any

from seleric_swarm.coordinator.contracts import AgentExecutionResult, MissionBudget, TaskSpec
from seleric_swarm.coordinator.execution.dispatcher import Dispatcher
from seleric_swarm.coordinator.execution.scheduler import get_ready_tasks, mark_done, mark_running
from seleric_swarm.coordinator.routing.invocation import AgentInvoker


class BudgetManager:
    def __init__(self, budgets: MissionBudget | dict[str, Any] | None = None) -> None:
        if isinstance(budgets, MissionBudget):
            self.budgets = budgets
        elif isinstance(budgets, dict):
            self.budgets = MissionBudget(**{k: v for k, v in budgets.items() if k in MissionBudget.model_fields})
        else:
            self.budgets = MissionBudget()

    def select(self, ready: list[TaskSpec], *, agent_calls_used: int = 0) -> list[TaskSpec]:
        remaining = max(0, self.budgets.max_agent_calls - agent_calls_used)
        capped = ready[: min(len(ready), self.budgets.max_parallel_tasks, remaining)]
        return capped


class ExecutionEngine:
    """Coordinator execution wave over a TaskSpec DAG."""

    def __init__(
        self,
        invoker: AgentInvoker,
        *,
        budgets: MissionBudget | dict[str, Any] | None = None,
    ) -> None:
        self.budget_manager = BudgetManager(budgets)
        self.dispatcher = Dispatcher(
            invoker,
            max_parallel=self.budget_manager.budgets.max_parallel_tasks,
        )

    async def run_wave(
        self,
        tasks: list[TaskSpec],
        state: dict[str, Any],
    ) -> tuple[list[TaskSpec], list[AgentExecutionResult], dict[str, Any]]:
        ready = get_ready_tasks(tasks)
        usage = dict(state.get("usage") or {})
        agent_calls = int(usage.get("agent_calls") or state.get("agent_calls") or 0)
        selected = self.budget_manager.select(ready, agent_calls_used=agent_calls)
        if not selected:
            return tasks, [], {"selected": [], "exhausted": not ready}

        tasks = mark_running(tasks, {t.task_id for t in selected})
        results = await self.dispatcher.execute(selected, state)
        for result in results:
            failed = result.status != "success"
            tasks = mark_done(tasks, result.task_id, failed=failed)
            agent_calls += 1

        usage["agent_calls"] = agent_calls
        patch = {
            "usage": usage,
            "agent_calls": agent_calls,
            "tasks": [t.model_dump() for t in tasks],
            "selected_task_ids": [t.task_id for t in selected],
        }
        return tasks, results, patch

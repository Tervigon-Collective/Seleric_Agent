"""Task dispatcher — invokes agents through AgentInvoker with retries."""

from __future__ import annotations

from typing import Any

from seleric_swarm.coordinator.contracts import AgentExecutionResult, TaskSpec
from seleric_swarm.coordinator.execution.parallel import run_parallel
from seleric_swarm.coordinator.execution.retry import classify_failure, should_retry
from seleric_swarm.coordinator.routing.invocation import AgentInvoker, build_context


class Dispatcher:
    def __init__(self, invoker: AgentInvoker, *, max_parallel: int = 4, max_retries: int = 2) -> None:
        self.invoker = invoker
        self.max_parallel = max_parallel
        self.max_retries = max_retries

    async def execute(
        self,
        tasks: list[TaskSpec],
        state: dict[str, Any],
    ) -> list[AgentExecutionResult]:
        async def _one(task: TaskSpec) -> AgentExecutionResult:
            agent_id = task.assigned_agent or "observer_agent"
            context = build_context(task, state)
            attempt = 0
            last: AgentExecutionResult | None = None
            while True:
                last = await self.invoker.invoke(agent_id, task, context)
                failure = classify_failure(last.error_code, status=last.status)
                if failure == "success":
                    return last
                if should_retry(failure, attempt=attempt, max_retries=self.max_retries):
                    attempt += 1
                    continue
                return last.model_copy(update={"status": failure})

        return await run_parallel([_one(t) for t in tasks], max_parallel=self.max_parallel)

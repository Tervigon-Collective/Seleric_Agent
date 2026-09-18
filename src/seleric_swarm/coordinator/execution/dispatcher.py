"""Task dispatcher — invokes agents through AgentInvoker with retries."""

from __future__ import annotations

import asyncio
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from seleric_swarm.coordinator.contracts import AgentExecutionResult, TaskSpec
from seleric_swarm.coordinator.execution.parallel import run_parallel
from seleric_swarm.coordinator.execution.retry import classify_failure, should_retry
from seleric_swarm.coordinator.routing.invocation import AgentInvoker, build_context
from seleric_swarm.observability.tracing import coordinator_task_metadata, traced_span


class Dispatcher:
    def __init__(
        self,
        invoker: AgentInvoker,
        *,
        max_parallel: int = 4,
        max_retries: int = 2,
        task_timeout_s: float = 45.0,
        cancellation: Any = None,
        retry_base_s: float = 0.25,
        retry_jitter_s: float = 0.25,
    ) -> None:
        self.invoker = invoker
        self.max_parallel = max_parallel
        self.max_retries = max_retries
        self.task_timeout_s = task_timeout_s
        self.cancellation = cancellation
        self.retry_base_s = max(0.0, retry_base_s)
        self.retry_jitter_s = max(0.0, retry_jitter_s)

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
            tracing = bool(state.get("langsmith_tracing"))
            base = dict(state.get("trace_base") or {})
            deadline = time.monotonic() + float(
                state.get("mission_timeout_s")
                or self.task_timeout_s * (self.max_retries + 1)
            )
            meta = coordinator_task_metadata(
                request_id=str(base.get("request_id") or state.get("request_id") or ""),
                session_id=str(base.get("session_id") or state.get("session_id") or ""),
                mission_id=str(state.get("mission_id") or task.mission_id),
                workflow_name=str(base.get("workflow_name") or "swarm_v2"),
                workflow_version=str(base.get("workflow_version") or "1.4.0"),
                agent_name=agent_id,
                agent_version=str(base.get("agent_version") or "1.4.0"),
                task_id=task.task_id,
                subquestion_id=task.subquestion_id,
                active_specialist=agent_id,
                mission_lead=state.get("mission_lead"),
                remediation_round=int(state.get("remediation_round") or 0),
                decomposition_id=state.get("current_decomposition_ref")
                or (state.get("decomposition_refs") or [None])[0],
                leadership_epoch=state.get("leadership_epoch"),
                synthetic=state.get("synthetic"),
            )
            while True:
                mission_id = str(state.get("mission_id") or task.mission_id)
                if self.cancellation is not None and self.cancellation.is_requested(mission_id):
                    from seleric_swarm.cancellation import MissionCancelledError

                    raise MissionCancelledError(f"mission {mission_id} was cancelled")
                with traced_span(
                    f"swarm.task.{agent_id}",
                    meta,
                    tracing,
                    inputs={
                        "task_id": task.task_id,
                        "objective": task.objective,
                        "attempt": attempt,
                    },
                    tags=["swarm_v2", "task", agent_id],
                ) as span:
                    try:
                        last = await asyncio.wait_for(
                            self.invoker.invoke(agent_id, task, context),
                            timeout=min(
                                self.task_timeout_s,
                                max(0.001, deadline - time.monotonic()),
                            ),
                        )
                    except TimeoutError:
                        last = AgentExecutionResult(
                            agent_id=agent_id,
                            task_id=task.task_id,
                            status="retryable_failure",
                            error_code="TIMEOUT",
                            error_message=(
                                f"Agent task exceeded the {self.task_timeout_s:g}s hard timeout"
                            ),
                        )
                    span.set_outputs(
                        {
                            "status": last.status,
                            "artifact_refs": list(last.artifact_refs or []),
                            "error_code": last.error_code,
                            "attempt": attempt,
                        }
                    )
                failure = classify_failure(last.error_code, status=last.status)
                if self.cancellation is not None and self.cancellation.is_requested(mission_id):
                    from seleric_swarm.cancellation import MissionCancelledError

                    raise MissionCancelledError(f"mission {mission_id} was cancelled")
                if failure == "success":
                    return last
                if should_retry(failure, attempt=attempt, max_retries=self.max_retries):
                    retry_after = (last.metadata or {}).get("retry_after")
                    delay = self.retry_base_s * (2**attempt) + random.uniform(
                        0.0, self.retry_jitter_s
                    )
                    if retry_after:
                        try:
                            delay = max(delay, float(retry_after))
                        except (TypeError, ValueError):
                            try:
                                parsed = parsedate_to_datetime(str(retry_after))
                                delay = max(
                                    delay,
                                    (
                                        parsed.astimezone(UTC) - datetime.now(UTC)
                                    ).total_seconds(),
                                )
                            except (TypeError, ValueError, OverflowError):
                                pass
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return last.model_copy(update={"status": failure})
                    await asyncio.sleep(min(max(0.0, delay), remaining))
                    attempt += 1
                    continue
                return last.model_copy(update={"status": failure})

        return await run_parallel([_one(t) for t in tasks], max_parallel=self.max_parallel)

"""Lease-fenced recovery and periodic execution for durable run attempts."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import inspect
import random
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunStatus,
)
from seleric_swarm.conversations.repositories import RunRepository


@dataclass(frozen=True)
class RecoveryResult:
    retryable: int = 0
    failed: int = 0
    already_retryable: int = 0


@dataclass(frozen=True)
class WorkerResult:
    claimed: int = 0
    completed: int = 0
    retryable: int = 0
    failed: int = 0


@dataclass(frozen=True)
class RunExecutionResult:
    status: RunStatus = RunStatus.COMPLETED
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    terminal_event: ActivityEvent | None = None
    on_committed: Callable[[RunStatus], Awaitable[None] | None] | None = None


class ResumableRunExecutor(Protocol):
    async def __call__(
        self, run: Run, attempt: RunAttempt
    ) -> RunExecutionResult | None: ...


class RetryableExecutionError(RuntimeError):
    pass


class RunWorkQueue(Protocol):
    async def enqueue(self, run_id: str) -> None: ...
    async def close(self) -> None: ...


class RunRecoveryService:
    def __init__(
        self,
        runs: RunRepository,
        *,
        retry_delay_s: float = 5.0,
        retry_jitter_s: float = 1.0,
    ) -> None:
        self._runs = runs
        self._retry_delay_s = retry_delay_s
        self._retry_jitter_s = retry_jitter_s

    def _next_attempt(
        self,
        run: Run,
        attempt: RunAttempt,
        *,
        moment: datetime,
        error_code: str,
        error_message: str,
        lease_expired_before: datetime | None = None,
    ) -> bool:
        retry_delay = self._retry_delay_s * (2 ** max(0, attempt.attempt_number - 1))
        if retry_delay > 0:
            retry_delay += random.uniform(0.0, max(0.0, self._retry_jitter_s))
        transition = self._runs.transition_failed_attempt(
            attempt.id,
            worker_id=attempt.worker_id if lease_expired_before is None else None,
            expected_version=attempt.version,
            lease_expired_before=lease_expired_before,
            now=moment,
            retry_delay_seconds=retry_delay,
            error_code=error_code,
            error_message=error_message,
        )
        return transition == "retryable"

    def fail_attempt(
        self,
        run: Run,
        attempt: RunAttempt,
        *,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> bool:
        return self._next_attempt(
            run,
            attempt,
            moment=now or datetime.now(UTC),
            error_code=error_code,
            error_message=error_message,
        )

    def recover_expired(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> RecoveryResult:
        moment = now or datetime.now(UTC)
        retryable = failed = already_retryable = 0
        for attempt in self._runs.list_recoverable(now=moment, limit=limit):
            if attempt.status is RunAttemptStatus.RETRYABLE:
                already_retryable += 1
                continue
            run = self._runs.get(attempt.run_id)
            if run is None:
                continue
            if run.status is RunStatus.CANCELLED:
                self._runs.compare_and_set_attempt(
                    attempt.id,
                    RunAttemptStatus.RUNNING,
                    RunAttemptStatus.CANCELLED,
                    expected_version=attempt.version,
                    lease_expired_before=moment,
                    now=moment,
                    error_code="CANCELLED",
                )
                continue
            can_retry = self._next_attempt(
                run,
                attempt,
                moment=moment,
                error_code="LEASE_EXPIRED",
                error_message="Worker lease expired before completion.",
                lease_expired_before=moment,
            )
            if can_retry:
                retryable += 1
            elif (
                (persisted_run := self._runs.get(run.id)) is not None
                and persisted_run.status is RunStatus.FAILED
            ):
                failed += 1
        return RecoveryResult(retryable, failed, already_retryable)


class RunRecoveryWorker:
    def __init__(
        self,
        runs: RunRepository,
        executor: ResumableRunExecutor,
        *,
        worker_id: str,
        lease_s: float = 60.0,
        heartbeat_s: float = 15.0,
        retry_delay_s: float = 5.0,
        retry_jitter_s: float = 1.0,
    ) -> None:
        self._runs = runs
        self._executor = executor
        self._worker_id = worker_id
        self._lease_s = lease_s
        self._heartbeat_s = min(max(0.1, heartbeat_s), max(0.1, lease_s / 2))
        self._recovery = RunRecoveryService(
            runs,
            retry_delay_s=retry_delay_s,
            retry_jitter_s=retry_jitter_s,
        )

    async def _heartbeat(self, attempt: RunAttempt) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_s)
            renewed = await asyncio.to_thread(
                self._runs.heartbeat,
                attempt.id,
                self._worker_id,
                self._lease_s,
                expected_version=attempt.version,
            )
            if not renewed:
                raise RuntimeError("run attempt lease was lost")

    async def _execute(self, run: Run, attempt: RunAttempt) -> str:
        heartbeat = asyncio.create_task(self._heartbeat(attempt))
        execution = asyncio.create_task(self._executor(run, attempt))
        try:
            done, _ = await asyncio.wait(
                {heartbeat, execution}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                await heartbeat
            execution_result = await execution
            outcome = execution_result or RunExecutionResult()
            if outcome.status is RunStatus.FAILED and outcome.retryable:
                raise RetryableExecutionError(
                    outcome.error_message or outcome.error_code or "transient mission failure"
                )
            persisted_run = await asyncio.to_thread(self._runs.get, run.id)
            if persisted_run is None:
                raise RuntimeError(f"run {run.id} disappeared during execution")
            final_status = (
                RunStatus.CANCELLED
                if persisted_run.status is RunStatus.CANCELLED
                else outcome.status
            )
            terminal_event = outcome.terminal_event or ActivityEvent(
                id=f"event_terminal_{run.id}_{attempt.id}",
                thread_id=run.thread_id,
                workspace_id=run.workspace_id,
                run_id=run.id,
                owner_user_id=run.requested_by_user_id,
                event_type="run.completed",
                payload={
                    "mission_id": run.mission_id,
                    "attempt_id": attempt.id,
                    "attempt_number": attempt.attempt_number,
                },
            )
            committed = await asyncio.to_thread(
                self._runs.finalize_attempt,
                attempt.id,
                worker_id=self._worker_id,
                expected_version=attempt.version,
                requested_status=final_status,
                terminal_event=terminal_event,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
            )
            if committed is None:
                raise RuntimeError("run attempt lease was lost before completion")
            committed_run, _, _ = committed
            committed_status = committed_run.status
            if committed_status not in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                raise RuntimeError(
                    f"run {run.id} rejected terminal status transition"
                )
            if outcome.on_committed is not None:
                maybe_awaitable = outcome.on_committed(committed_status)
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
            return "completed" if committed_status is RunStatus.COMPLETED else "failed"
        except asyncio.CancelledError:
            execution.cancel()
            raise
        except Exception as exc:
            execution.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await execution
            retryable = await asyncio.to_thread(
                self._recovery.fail_attempt,
                run,
                attempt,
                error_code="EXECUTION_FAILED",
                error_message=str(exc),
            )
            if not retryable:
                persisted = self._runs.get(run.id)
                if persisted is not None and persisted.status is RunStatus.COMPLETED:
                    return "completed"
                if persisted is not None and persisted.status in {
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    return "failed"
            return "retryable" if retryable else "failed"
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def run_once(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> WorkerResult:
        scan_moment = now or datetime.now(UTC)
        await asyncio.to_thread(
            self._recovery.recover_expired, now=scan_moment, limit=limit
        )
        claimed = completed = retryable = failed = 0
        candidates = await asyncio.to_thread(
            self._runs.list_recoverable, now=scan_moment, limit=limit
        )
        for candidate in candidates:
            if candidate.status is not RunAttemptStatus.RETRYABLE:
                continue
            claim_moment = now or datetime.now(UTC)
            run = await asyncio.to_thread(self._runs.get, candidate.run_id)
            if (
                run is None
                or run.status is RunStatus.CANCELLED
                or (run.next_retry_at is not None and run.next_retry_at > claim_moment)
            ):
                continue
            attempt = await asyncio.to_thread(
                self._runs.claim,
                run.id,
                self._worker_id,
                self._lease_s,
                now=claim_moment,
            )
            if attempt is None:
                continue
            claimed += 1
            outcome = await self._execute(run, attempt)
            completed += outcome == "completed"
            retryable += outcome == "retryable"
            failed += outcome == "failed"
        return WorkerResult(claimed, completed, retryable, failed)

    async def run_forever(
        self, *, poll_interval_s: float = 5.0, limit: int = 100
    ) -> None:
        while True:
            await self.run_once(limit=limit)
            await asyncio.sleep(max(0.1, poll_interval_s))


class DurablePollingRunQueue:
    """The durable queue is the QUEUED run/RETRYABLE attempt rows themselves."""

    async def enqueue(self, run_id: str) -> None:
        del run_id

    async def close(self) -> None:
        return None


class InProcessRunQueue:
    """Development/test queue using the same claim and fencing worker path."""

    def __init__(self, worker: RunRecoveryWorker) -> None:
        self._worker = worker
        self._tasks: set[asyncio.Task[WorkerResult]] = set()

    async def enqueue(self, run_id: str) -> None:
        del run_id
        task = asyncio.create_task(self._worker.run_once())
        self._tasks.add(task)

        def _finished(done: asyncio.Task[WorkerResult]) -> None:
            self._tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(_finished)
        await asyncio.sleep(0)

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def flush(self) -> None:
        """Wait for currently scheduled work without closing the reusable queue."""
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


def build_run_queue(
    runtime: Any,
    executor: ResumableRunExecutor,
) -> RunWorkQueue:
    if runtime.settings.persistence_backend == "postgres":
        return DurablePollingRunQueue()
    repositories = runtime.conversations
    if repositories is None:
        raise RuntimeError("conversation repositories are not configured")
    worker_id = runtime.settings.run_worker_id or f"inprocess-{id(runtime):x}"
    return InProcessRunQueue(
        RunRecoveryWorker(
            repositories.runs,
            executor,
            worker_id=worker_id,
            lease_s=runtime.settings.run_lease_s,
            heartbeat_s=runtime.settings.run_heartbeat_s,
            retry_delay_s=runtime.settings.run_retry_delay_s,
            retry_jitter_s=runtime.settings.run_retry_jitter_s,
        )
    )


async def _main() -> None:
    from seleric_swarm.bootstrap import build_runtime

    parser = argparse.ArgumentParser(description="Recover and execute durable Seleric runs")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--executor",
        help=(
            "Dotted executor factory as module:attribute; receives runtime and "
            "returns an async (run, attempt) callback"
        ),
    )
    args = parser.parse_args()
    runtime = build_runtime()
    if runtime.conversations is None:
        raise RuntimeError("conversation repositories are not configured")
    provider = runtime.checkpoint_provider
    if provider is not None:
        await provider.setup()

    recovery = RunRecoveryService(
        runtime.conversations.runs,
        retry_delay_s=runtime.settings.run_retry_delay_s,
        retry_jitter_s=runtime.settings.run_retry_jitter_s,
    )
    worker = None
    if args.executor:
        module_name, separator, attribute = args.executor.partition(":")
        if not separator:
            raise ValueError("--executor must use module:attribute syntax")
        executor_factory = getattr(importlib.import_module(module_name), attribute)
        executor = executor_factory(runtime)
        worker_id = runtime.settings.run_worker_id or (
            f"{socket.gethostname()}-{id(runtime):x}"
        )
        worker = RunRecoveryWorker(
            runtime.conversations.runs,
            executor,
            worker_id=worker_id,
            lease_s=runtime.settings.run_lease_s,
            heartbeat_s=runtime.settings.run_heartbeat_s,
            retry_delay_s=runtime.settings.run_retry_delay_s,
            retry_jitter_s=runtime.settings.run_retry_jitter_s,
        )
    try:
        if args.once:
            if worker is not None:
                worker_result = await worker.run_once(limit=args.limit)
                print(
                    f"claimed={worker_result.claimed} completed={worker_result.completed} "
                    f"retryable={worker_result.retryable} failed={worker_result.failed}"
                )
            else:
                recovery_result = recovery.recover_expired(limit=args.limit)
                print(
                    f"retryable={recovery_result.retryable} failed={recovery_result.failed} "
                    f"already_retryable={recovery_result.already_retryable}"
                )
        else:
            if worker is not None:
                await worker.run_forever(
                    poll_interval_s=args.interval, limit=args.limit
                )
            else:
                while True:
                    recovery.recover_expired(limit=args.limit)
                    await asyncio.sleep(max(0.1, args.interval))
    finally:
        if provider is not None:
            await provider.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()

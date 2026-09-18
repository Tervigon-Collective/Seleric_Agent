from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunStatus,
    Thread,
)
from seleric_swarm.conversations.events import ActivityEventSink
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.recovery import RunExecutionResult, RunRecoveryWorker


def _records(*, max_attempts: int = 2):
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    run = repositories.runs.create(
        Run(
            thread_id=thread.id,
            workspace_id="w",
            requested_by_user_id="u",
            current_attempt=1,
            max_attempts=max_attempts,
        )
    )
    attempt = repositories.runs.add_attempt(
        RunAttempt(
            run_id=run.id,
            attempt_number=1,
            status=RunAttemptStatus.RETRYABLE,
        )
    )
    repositories.runs.add_outbox(run.id)
    return repositories, run, attempt


def _terminal(run: Run, attempt: RunAttempt) -> ActivityEvent:
    return ActivityEvent(
        id=f"terminal_{run.id}_{attempt.id}",
        thread_id=run.thread_id,
        workspace_id=run.workspace_id,
        run_id=run.id,
        owner_user_id=run.requested_by_user_id,
        event_type="run.completed",
        payload={"attempt_id": attempt.id},
    )


def test_atomic_finalize_fences_stale_worker_and_commits_all_records():
    repositories, run, _ = _records()
    claimed = repositories.runs.claim(run.id, "winner", 30)
    assert claimed is not None

    stale = repositories.runs.finalize_attempt(
        claimed.id,
        worker_id="stale",
        expected_version=claimed.version,
        requested_status=RunStatus.COMPLETED,
        terminal_event=_terminal(run, claimed),
    )
    assert stale is None
    assert repositories.runs.get(run.id).status is RunStatus.QUEUED
    assert repositories.runs.list_pending_outbox() == [run.id]
    assert repositories.runs.list_events(run.id) == []

    committed = repositories.runs.finalize_attempt(
        claimed.id,
        worker_id="winner",
        expected_version=claimed.version,
        requested_status=RunStatus.COMPLETED,
        terminal_event=_terminal(run, claimed),
    )
    assert committed is not None
    committed_run, committed_attempt, event = committed
    assert committed_run.status is RunStatus.COMPLETED
    assert committed_attempt.status is RunAttemptStatus.COMPLETED
    assert repositories.runs.list_events(run.id) == [event]
    assert repositories.runs.list_pending_outbox() == []


@pytest.mark.asyncio
async def test_cancellation_barrier_wins_before_terminal_commit():
    repositories, run, _ = _records()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def execute(_run: Run, attempt: RunAttempt) -> RunExecutionResult:
        entered.set()
        await release.wait()
        return RunExecutionResult(
            status=RunStatus.COMPLETED,
            terminal_event=_terminal(run, attempt),
        )

    worker = RunRecoveryWorker(
        repositories.runs,
        execute,
        worker_id="worker",
        retry_delay_s=0,
    )
    task = asyncio.create_task(worker.run_once())
    await entered.wait()
    cancelled = ActivityEvent(
        id=f"cancel_{run.id}",
        thread_id=run.thread_id,
        workspace_id=run.workspace_id,
        run_id=run.id,
        event_type="run.cancelled",
    )
    assert repositories.runs.cancel(run.id, terminal_event=cancelled)
    release.set()
    await task

    assert repositories.runs.get(run.id).status is RunStatus.CANCELLED
    terminal = [
        event
        for event in repositories.runs.list_events(run.id)
        if event.event_type in {"run.completed", "run.failed", "run.cancelled"}
    ]
    assert [event.event_type for event in terminal] == ["run.cancelled"]


def test_retry_transition_is_atomic_and_idempotently_numbered():
    repositories, run, _ = _records(max_attempts=2)
    claimed = repositories.runs.claim(run.id, "worker", 30)
    assert claimed is not None
    now = datetime.now(UTC)
    assert (
        repositories.runs.transition_failed_attempt(
            claimed.id,
            expected_version=claimed.version,
            retry_delay_seconds=0,
            now=now,
            error_code="TIMEOUT",
            error_message="injected",
            worker_id="worker",
        )
        == "retryable"
    )
    assert (
        repositories.runs.transition_failed_attempt(
            claimed.id,
            expected_version=claimed.version,
            retry_delay_seconds=0,
            now=now,
            error_code="TIMEOUT",
            error_message="duplicate",
            worker_id="worker",
        )
        == "stale"
    )
    attempts = repositories.runs.list_recoverable(now=now)
    assert [(item.id, item.attempt_number) for item in attempts] == [
        (f"attempt_retry_{run.id}_2", 2)
    ]


@pytest.mark.asyncio
async def test_notifier_failures_do_not_rollback_persisted_events():
    repositories, run, _ = _records()

    class BrokenNotifier:
        def publish(self, _run_id: str, _sequence: int) -> None:
            raise ConnectionError("publish fault")

        async def wait(
            self, _run_id: str, _after_sequence: int, _timeout: float
        ) -> None:
            raise ConnectionError("wait fault")

    sink = ActivityEventSink(repositories.runs, BrokenNotifier())
    event = sink.emit(run, "run.started")
    assert repositories.runs.list_events(run.id) == [event]
    with pytest.raises(ConnectionError):
        await sink.notifier.wait(run.id, event.sequence, 0)

"""The recovery worker dispatches continuously and drains on shutdown (2026-10-07).

Before: each poll waited for its whole batch, so a run submitted behind a long
mission queued until it ended, and a redeploy (SIGTERM, then SIGKILL after 10s)
killed the running mission, which restarted from scratch after its lease expired.
"""

from __future__ import annotations

import asyncio

import pytest

from seleric_swarm.conversations.contracts import (
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunStatus,
    Thread,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.recovery import RunExecutionResult, RunRecoveryWorker


def _add_run(repositories, thread, i: int) -> str:
    run = repositories.runs.create(
        Run(
            thread_id=thread.id,
            workspace_id="w",
            requested_by_user_id="u",
            mission_id=f"mission-{i}",
            current_attempt=1,
            max_attempts=1,
        )
    )
    repositories.runs.add_attempt(RunAttempt(run_id=run.id, attempt_number=1, status=RunAttemptStatus.RETRYABLE))
    return run.id


class _Gate:
    """Executor whose missions run until released, recording which started."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.release: dict[str, asyncio.Event] = {}

    async def __call__(self, run, attempt):
        self.started.append(run.id)
        event = self.release.setdefault(run.id, asyncio.Event())
        await event.wait()
        return RunExecutionResult(status=RunStatus.COMPLETED)

    def let_finish(self, run_id: str) -> None:
        self.release.setdefault(run_id, asyncio.Event()).set()


async def _until(predicate, timeout: float = 2.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout)


@pytest.mark.asyncio
async def test_a_new_run_starts_while_an_earlier_mission_is_still_running():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    first = _add_run(repositories, thread, 1)
    gate = _Gate()
    worker = RunRecoveryWorker(repositories.runs, gate, worker_id="w1", retry_delay_s=0, max_concurrency=2)
    stop = asyncio.Event()
    loop = asyncio.create_task(worker.run_forever(poll_interval_s=0.02, stop=stop, drain_timeout_s=5))
    await _until(lambda: first in gate.started)
    second = _add_run(repositories, thread, 2)
    await _until(lambda: second in gate.started)  # not blocked behind the first
    gate.let_finish(first)
    gate.let_finish(second)
    stop.set()
    await asyncio.wait_for(loop, 5)
    assert repositories.runs.get(first).status is RunStatus.COMPLETED
    assert repositories.runs.get(second).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_stop_drains_the_running_mission_and_claims_nothing_new():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    running = _add_run(repositories, thread, 1)
    gate = _Gate()
    worker = RunRecoveryWorker(repositories.runs, gate, worker_id="w1", retry_delay_s=0, max_concurrency=2)
    stop = asyncio.Event()
    loop = asyncio.create_task(worker.run_forever(poll_interval_s=0.02, stop=stop, drain_timeout_s=5))
    await _until(lambda: running in gate.started)
    stop.set()  # SIGTERM
    queued = _add_run(repositories, thread, 2)
    await asyncio.sleep(0.1)
    assert not loop.done()  # still draining the running mission
    gate.let_finish(running)
    await asyncio.wait_for(loop, 5)
    assert repositories.runs.get(running).status is RunStatus.COMPLETED
    assert queued not in gate.started  # left queued for the next worker


@pytest.mark.asyncio
async def test_a_mission_past_the_drain_timeout_is_abandoned_to_lease_expiry():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    stuck = _add_run(repositories, thread, 1)
    gate = _Gate()
    worker = RunRecoveryWorker(repositories.runs, gate, worker_id="w1", retry_delay_s=0)
    stop = asyncio.Event()
    loop = asyncio.create_task(worker.run_forever(poll_interval_s=0.02, stop=stop, drain_timeout_s=0.1))
    await _until(lambda: stuck in gate.started)
    stop.set()
    await asyncio.wait_for(loop, 5)
    assert repositories.runs.get(stuck).status is not RunStatus.COMPLETED

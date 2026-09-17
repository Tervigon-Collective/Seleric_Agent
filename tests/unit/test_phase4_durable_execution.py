from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from seleric_swarm.api import async_missions
from seleric_swarm.cancellation import InMemoryCancellationBackend
from seleric_swarm.checkpointing import (
    InMemoryCheckpointProvider,
    NoOpCheckpointProvider,
    build_checkpoint_provider,
)
from seleric_swarm.conversations.contracts import (
    Run,
    RunAttempt,
    RunAttemptStatus,
    Thread,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.recovery import RunRecoveryService


def _run_records(*, max_attempts: int = 3):
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
    attempt = repositories.runs.add_attempt(RunAttempt(run_id=run.id, attempt_number=1))
    return repositories, run, attempt


def test_claim_heartbeat_and_cas_are_owner_safe():
    repositories, run, attempt = _run_records()
    now = datetime.now(UTC)
    claimed = repositories.runs.claim(run.id, "worker-a", 10, now=now)
    assert claimed is not None
    assert claimed.worker_id == "worker-a"
    assert claimed.lease_expires_at == now + timedelta(seconds=10)
    assert repositories.runs.claim(run.id, "worker-b", 10, now=now) is None
    assert not repositories.runs.heartbeat(attempt.id, "worker-b", 10, now=now)
    assert repositories.runs.heartbeat(attempt.id, "worker-a", 20, now=now)
    assert (
        repositories.runs.compare_and_set_attempt(
            attempt.id,
            RunAttemptStatus.RUNNING,
            RunAttemptStatus.COMPLETED,
            worker_id="worker-b",
        )
        is None
    )
    assert repositories.runs.compare_and_set_attempt(
        attempt.id,
        RunAttemptStatus.RUNNING,
        RunAttemptStatus.COMPLETED,
        worker_id="worker-a",
    )


def test_cancel_is_visible_and_atomic():
    repositories, run, attempt = _run_records()
    backend = InMemoryCancellationBackend()
    backend.request("mission-1")
    assert backend.is_requested("mission-1")
    assert repositories.runs.cancel(run.id)
    assert not repositories.runs.cancel(run.id)
    assert repositories.runs.get(run.id).status.value == "CANCELLED"
    assert attempt.id not in {
        item.id for item in repositories.runs.list_recoverable()
    }
    backend.clear("mission-1")
    assert not backend.is_requested("mission-1")


def test_recovery_marks_expired_attempt_retryable_or_failed():
    now = datetime.now(UTC)
    repositories, run, _ = _run_records(max_attempts=2)
    claimed = repositories.runs.claim(run.id, "dead-worker", 1, now=now - timedelta(seconds=2))
    assert claimed is not None
    result = RunRecoveryService(repositories.runs).recover_expired(now=now)
    assert result.retryable == 1
    assert repositories.runs.get(run.id).status.value == "QUEUED"

    repositories2, run2, _ = _run_records(max_attempts=1)
    repositories2.runs.claim(run2.id, "dead-worker", 1, now=now - timedelta(seconds=2))
    result2 = RunRecoveryService(repositories2.runs).recover_expired(now=now)
    assert result2.failed == 1
    assert repositories2.runs.get(run2.id).status.value == "FAILED"


def test_checkpoint_provider_config_and_default():
    assert isinstance(build_checkpoint_provider("none"), NoOpCheckpointProvider)
    with pytest.raises(ValueError, match="database_url"):
        build_checkpoint_provider("postgres")
    provider = InMemoryCheckpointProvider()
    assert provider.get_checkpointer() is not None
    assert provider.config(thread_id="mission", run_id="run") == {
        "configurable": {"thread_id": "mission", "checkpoint_ns": "run"}
    }


@pytest.mark.asyncio
async def test_async_mission_timeout_is_persisted(runtime, monkeypatch):
    async def _slow(*_args, **_kwargs):
        await asyncio.sleep(1)

    runtime.settings.mission_timeout_s = 0.01
    monkeypatch.setattr(async_missions, "run_any_mission", _slow)
    mission_id = "MS-timeout"
    async_missions.seed_running_mission(
        runtime,
        mission_id=mission_id,
        query="slow",
        request_id="request",
        session_id="session",
    )
    await async_missions.run_mission_job(
        runtime,
        mission_id=mission_id,
        query="slow",
        timezone="UTC",
        as_of=None,
        session_id="session",
        request_id="request",
        full_diagnostic=False,
        full_prediction=False,
        full_skeptic=False,
        full_strategy=False,
        execution_mode="production",
    )
    assert runtime.store.get_raw(mission_id)["error_code"] == "MISSION_TIMEOUT"


def test_legacy_cancel_helpers_remain_compatible():
    async_missions.request_cancel("legacy")
    assert async_missions.is_cancel_requested("legacy")
    async_missions.clear_cancel("legacy")
    assert not async_missions.is_cancel_requested("legacy")

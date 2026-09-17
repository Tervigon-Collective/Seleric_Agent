from __future__ import annotations

from types import SimpleNamespace

import pytest

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.cancellation import InMemoryCancellationBackend
from seleric_swarm.conversations.contracts import (
    Message,
    MessagePart,
    MessagePartType,
    MessageRole,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunStatus,
    Thread,
)
from seleric_swarm.conversations.events import ActivityEventSink, InMemoryEventNotifier
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.recovery import RunRecoveryWorker


class _RawStore:
    def __init__(self) -> None:
        self.payloads: dict[str, dict] = {}

    def get_raw(self, mission_id: str | None):
        return self.payloads.get(mission_id or "")

    def get(self, _mission_id: str):
        return None


def _submission_runtime():
    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(
        Thread(workspace_id="workspace", owner_user_id="user")
    )
    assistant = repositories.messages.create(
        Message(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            role=MessageRole.ASSISTANT,
            parts=[
                MessagePart(
                    type=MessagePartType.AGENT_STATUS,
                    content="Working…",
                )
            ],
        )
    )
    run = repositories.runs.create(
        Run(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            requested_by_user_id=thread.owner_user_id,
            mission_id="mission-recovery",
            current_attempt=1,
            max_attempts=2,
            metadata={
                "submission": {
                    "query": "Why did churn increase?",
                    "timezone": "UTC",
                    "request_id": "request-recovery",
                    "execution_mode": "production",
                    "assistant_message_id": assistant.id,
                }
            },
        )
    )
    assistant = repositories.messages.update(
        assistant.model_copy(update={"run_id": run.id})
    )
    repositories.runs.add_attempt(
        RunAttempt(
            run_id=run.id,
            attempt_number=1,
            status=RunAttemptStatus.RETRYABLE,
        )
    )
    store = _RawStore()
    runtime = SimpleNamespace(
        conversations=repositories,
        store=store,
        cancellation=InMemoryCancellationBackend(),
        activity_events=ActivityEventSink(
            repositories.runs, InMemoryEventNotifier()
        ),
        settings=SimpleNamespace(
            workflow_version="test",
            run_worker_id="",
            run_lease_s=30.0,
            run_heartbeat_s=5.0,
            run_retry_delay_s=0.0,
            persistence_backend="memory",
        ),
    )
    return runtime, repositories, run, assistant


@pytest.mark.asyncio
async def test_submission_executor_completes_with_single_terminal_event(monkeypatch):
    runtime, repositories, run, assistant = _submission_runtime()

    async def complete(runtime_arg, *, mission_id, **_kwargs):
        runtime_arg.store.payloads[mission_id] = {
            "status": "completed",
            "final_response": "Recovered answer",
            "events": [],
        }

    monkeypatch.setattr(conversations_api, "run_mission_job", complete)
    worker = RunRecoveryWorker(
        repositories.runs,
        conversations_api.build_submission_executor(runtime),
        worker_id="recovery-worker",
        retry_delay_s=0,
    )
    first = await worker.run_once()
    second = await worker.run_once()
    assert first.completed == 1
    assert second.claimed == 0
    assert repositories.runs.get(run.id).status is RunStatus.COMPLETED
    assert repositories.messages.get(assistant.id).parts[0].content == "Recovered answer"
    terminal = [
        event
        for event in repositories.runs.list_events(run.id)
        if event.event_type in {"run.completed", "run.failed", "run.cancelled"}
    ]
    assert [event.event_type for event in terminal] == ["run.completed"]


@pytest.mark.asyncio
async def test_submission_crash_is_recovered_from_persisted_payload(monkeypatch):
    runtime, repositories, run, _ = _submission_runtime()
    calls = 0

    async def crash_then_complete(runtime_arg, *, mission_id, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("process crashed")
        runtime_arg.store.payloads[mission_id] = {
            "status": "completed",
            "final_response": "Resumed",
            "events": [],
        }

    monkeypatch.setattr(
        conversations_api, "run_mission_job", crash_then_complete
    )
    executor = conversations_api.build_submission_executor(runtime)
    crashed = await RunRecoveryWorker(
        repositories.runs,
        executor,
        worker_id="first-worker",
        retry_delay_s=0,
    ).run_once()
    resumed = await RunRecoveryWorker(
        repositories.runs,
        executor,
        worker_id="second-worker",
        retry_delay_s=0,
    ).run_once()
    assert crashed.retryable == 1
    assert resumed.completed == 1
    assert calls == 2
    assert repositories.runs.get(run.id).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_submission_cancellation_before_execution_emits_once(monkeypatch):
    runtime, repositories, run, _ = _submission_runtime()
    called = False

    async def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(conversations_api, "run_mission_job", should_not_run)
    runtime.cancellation.request(run.mission_id)
    worker = RunRecoveryWorker(
        repositories.runs,
        conversations_api.build_submission_executor(runtime),
        worker_id="cancel-worker",
        retry_delay_s=0,
    )
    await worker.run_once()
    await worker.run_once()
    assert not called
    assert repositories.runs.get(run.id).status is RunStatus.CANCELLED
    terminal = [
        event.event_type
        for event in repositories.runs.list_events(run.id)
        if event.event_type in {"run.completed", "run.failed", "run.cancelled"}
    ]
    assert terminal == ["run.cancelled"]


@pytest.mark.asyncio
async def test_submission_failure_uses_terminal_fenced_cas(monkeypatch):
    runtime, repositories, run, _ = _submission_runtime()

    async def fail(runtime_arg, *, mission_id, **_kwargs):
        runtime_arg.store.payloads[mission_id] = {
            "status": "failed",
            "error_code": "UPSTREAM_FAILED",
            "error_message": "upstream unavailable",
            "events": [],
        }

    monkeypatch.setattr(conversations_api, "run_mission_job", fail)
    worker = RunRecoveryWorker(
        repositories.runs,
        conversations_api.build_submission_executor(runtime),
        worker_id="failure-worker",
        retry_delay_s=0,
    )
    result = await worker.run_once()
    assert result.failed == 1
    assert repositories.runs.get(run.id).status is RunStatus.FAILED
    terminal = [
        event.event_type
        for event in repositories.runs.list_events(run.id)
        if event.event_type in {"run.completed", "run.failed", "run.cancelled"}
    ]
    assert terminal == ["run.failed"]

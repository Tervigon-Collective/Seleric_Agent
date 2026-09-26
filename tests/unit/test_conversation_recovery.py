from __future__ import annotations

import asyncio
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
from seleric_swarm.recovery import (
    InProcessRunQueue,
    RunExecutionResult,
    RunRecoveryWorker,
)


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
                    "execution_mode": "development",
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
async def test_progress_emitted_during_a_run_is_streamed_as_activity_events(monkeypatch):
    from seleric_swarm.agent.progress import emit_progress, has_progress_sink

    runtime, repositories, run, _ = _submission_runtime()
    registered_during_run = False

    async def working(runtime_arg, *, mission_id, **_kwargs):
        nonlocal registered_during_run
        registered_during_run = has_progress_sink(mission_id)
        emit_progress(mission_id, "agent.tool_started", "Fetching metric data", {"tool": "query_metrics"})
        runtime_arg.store.payloads[mission_id] = {
            "status": "completed",
            "final_response": "done",
            "events": [],
        }

    monkeypatch.setattr(conversations_api, "run_mission_job", working)
    worker = RunRecoveryWorker(
        repositories.runs,
        conversations_api.build_submission_executor(runtime),
        worker_id="recovery-worker",
        retry_delay_s=0,
    )
    await worker.run_once()

    events = repositories.runs.list_events(run.id)
    progress_events = [e for e in events if e.event_type == "agent.tool_started"]
    assert registered_during_run
    assert [(e.summary, e.payload["tool"]) for e in progress_events] == [
        ("Fetching metric data", "query_metrics")
    ]
    assert not has_progress_sink(run.mission_id or "")
    order = [e.event_type for e in events]
    assert order.index("agent.tool_started") < order.index("run.completed")


def test_cancelling_a_run_finalizes_its_pending_placeholder():
    _, repositories, run, assistant = _submission_runtime()

    conversations_api._finalize_cancelled_placeholder(repositories, run)

    parts = repositories.messages.get(assistant.id).parts
    assert [(p.type, p.content) for p in parts] == [(MessagePartType.WARNING, "Run cancelled.")]


def test_cancelling_never_overwrites_an_answered_reply():
    _, repositories, run, assistant = _submission_runtime()
    answered = repositories.messages.get(assistant.id).model_copy(
        update={"parts": [MessagePart(type=MessagePartType.TEXT, content="Real answer")]}
    )
    repositories.messages.update(answered)

    conversations_api._finalize_cancelled_placeholder(repositories, run)

    assert repositories.messages.get(assistant.id).parts[0].content == "Real answer"


@pytest.mark.asyncio
async def test_in_process_queue_drives_a_retryable_failure_to_a_terminal_state(monkeypatch):
    """Dev backend has no external poller: a backoff-scheduled retry must still run."""
    runtime, repositories, run, assistant = _submission_runtime()
    calls = 0

    async def fail_then_complete(runtime_arg, *, mission_id, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            runtime_arg.store.payloads[mission_id] = {
                "status": "failed",
                "error_code": "V3_AGENT_FAILED",
                "final_response": "The agent could not complete this question.",
                "events": [],
            }
            return
        runtime_arg.store.payloads[mission_id] = {
            "status": "completed",
            "final_response": "Second try worked",
            "events": [],
        }

    monkeypatch.setattr(conversations_api, "run_mission_job", fail_then_complete)
    worker = RunRecoveryWorker(
        repositories.runs,
        conversations_api.build_submission_executor(runtime),
        worker_id="recovery-worker",
        retry_delay_s=0.2,
        retry_jitter_s=0.0,
    )
    queue = InProcessRunQueue(worker)
    await queue.enqueue(run.id)
    await queue.flush()

    assert calls == 2
    assert repositories.runs.get(run.id).status is RunStatus.COMPLETED
    assert repositories.messages.get(assistant.id).parts[0].content == "Second try worked"


def _seed_retryable_runs(repositories, n: int) -> None:
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    for i in range(n):
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
        repositories.runs.add_attempt(
            RunAttempt(run_id=run.id, attempt_number=1, status=RunAttemptStatus.RETRYABLE)
        )


def _concurrency_probe():
    """Executor that records the peak number of overlapping executions."""
    state = {"live": 0, "peak": 0}

    async def executor(run, attempt):
        state["live"] += 1
        state["peak"] = max(state["peak"], state["live"])
        await asyncio.sleep(0.05)
        state["live"] -= 1
        return RunExecutionResult(status=RunStatus.COMPLETED)

    return executor, state


@pytest.mark.asyncio
async def test_run_once_executes_claimed_missions_concurrently():
    # Latency #5: a burst of submissions ran strictly serially (live: L4 waited
    # 106s behind the others). With max_concurrency>1 the claimed missions overlap.
    repositories = build_in_memory_repositories()
    _seed_retryable_runs(repositories, 3)
    executor, state = _concurrency_probe()
    worker = RunRecoveryWorker(
        repositories.runs, executor, worker_id="w1", retry_delay_s=0, max_concurrency=3
    )
    result = await worker.run_once()
    assert result.claimed == 3 and result.completed == 3
    assert state["peak"] == 3  # all three overlapped, not run one-at-a-time


@pytest.mark.asyncio
async def test_run_once_serial_by_default():
    # Default max_concurrency=1 preserves the original one-at-a-time behavior.
    repositories = build_in_memory_repositories()
    _seed_retryable_runs(repositories, 3)
    executor, state = _concurrency_probe()
    worker = RunRecoveryWorker(repositories.runs, executor, worker_id="w1", retry_delay_s=0)
    result = await worker.run_once()
    assert result.claimed == 3
    assert state["peak"] == 1  # never more than one at a time


@pytest.mark.asyncio
async def test_insufficient_evidence_verdict_is_not_retried(monkeypatch):
    """P0-1 (L4 "net revenue by channel", 3× re-run / 21.7 min): a verdict code
    like INSUFFICIENT_EVIDENCE is deterministic — re-running the whole mission
    re-burns it to the identical answer. It must fail closed on the first
    attempt even though max_attempts leaves room, unlike a transient code."""
    runtime, repositories, run, assistant = _submission_runtime()
    calls = 0

    async def fail_insufficient(runtime_arg, *, mission_id, **_kwargs):
        nonlocal calls
        calls += 1
        runtime_arg.store.payloads[mission_id] = {
            "status": "failed",
            "error_code": "INSUFFICIENT_EVIDENCE",
            "final_response": "Net revenue by channel: …",
            "events": [],
        }

    monkeypatch.setattr(conversations_api, "run_mission_job", fail_insufficient)
    worker = RunRecoveryWorker(
        repositories.runs,
        conversations_api.build_submission_executor(runtime),
        worker_id="recovery-worker",
        retry_delay_s=0.0,
        retry_jitter_s=0.0,
    )
    queue = InProcessRunQueue(worker)
    await queue.enqueue(run.id)
    await queue.flush()

    assert calls == 1  # no whole-mission re-run despite attempts remaining
    assert repositories.runs.get(run.id).status is RunStatus.FAILED


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


def _always_fail(runtime_arg, *, mission_id, **_kwargs):
    runtime_arg.store.payloads[mission_id] = {
        "status": "failed",
        "error_code": "V3_AGENT_FAILED",
        "final_response": "The agent could not complete this question. Please retry.",
        "events": [],
    }


@pytest.mark.asyncio
async def test_failed_attempt_with_retry_left_keeps_placeholder_pending(monkeypatch):
    # Live 2026-09-24 run_27159f6a: attempt 1 wrote "could not complete" into the
    # transcript while attempt 2 was still running.
    runtime, repositories, run, assistant = _submission_runtime()

    async def fail(runtime_arg, **kwargs):
        _always_fail(runtime_arg, **kwargs)

    monkeypatch.setattr(conversations_api, "run_mission_job", fail)
    worker = RunRecoveryWorker(
        repositories.runs,
        conversations_api.build_submission_executor(runtime),
        worker_id="recovery-worker",
        retry_delay_s=0,
        retry_jitter_s=0.0,
    )
    await worker.run_once()

    assert repositories.runs.get(run.id).status is not RunStatus.FAILED
    parts = repositories.messages.get(assistant.id).parts
    assert all(part.type.value != "WARNING" for part in parts)
    events = repositories.runs.list_events(run.id)
    retrying = [e for e in events if e.event_type == "agent.retrying"]
    assert retrying and "attempt 2 of" in (retrying[0].summary or "")
    # Any partially streamed answer from the failed attempt is cleared.
    resets = [e for e in events if e.event_type == "answer.reset"]
    assert resets and resets[0].payload["message_id"] == assistant.id


@pytest.mark.asyncio
async def test_last_failed_attempt_writes_the_warning(monkeypatch):
    runtime, repositories, run, assistant = _submission_runtime()

    async def fail(runtime_arg, **kwargs):
        _always_fail(runtime_arg, **kwargs)

    monkeypatch.setattr(conversations_api, "run_mission_job", fail)
    worker = RunRecoveryWorker(
        repositories.runs,
        conversations_api.build_submission_executor(runtime),
        worker_id="recovery-worker",
        retry_delay_s=0,
        retry_jitter_s=0.0,
    )
    for _ in range(run.max_attempts):
        await worker.run_once()

    assert repositories.runs.get(run.id).status is RunStatus.FAILED
    parts = repositories.messages.get(assistant.id).parts
    assert parts[0].type.value == "WARNING"
    assert "could not complete" in str(parts[0].content)

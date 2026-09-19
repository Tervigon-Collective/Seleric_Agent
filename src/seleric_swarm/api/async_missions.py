"""Async mission acceptance — seed running placeholder, finish in background."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from seleric_swarm.agent.runner import run_v3_mission
from seleric_swarm.api.status import TERMINAL_STATUSES
from seleric_swarm.cancellation import InMemoryCancellationBackend
from seleric_swarm.contracts.lookup import MissionResult, TraceInfo
from seleric_swarm.conversations.contracts import (
    Message,
    MessagePart,
    MessagePartType,
    MessageRole,
    Run,
    RunAttempt,
    RunAttemptStatus,
    Thread,
)
from seleric_swarm.orchestration.dispatch import run_any_mission
from seleric_swarm.runtime import SwarmRuntime

_log = logging.getLogger("seleric.api.async_missions")

# Re-export for callers / tests
_TERMINAL = TERMINAL_STATUSES

_default_cancellation = InMemoryCancellationBackend()


def _cancellation(runtime: SwarmRuntime | None = None):
    return getattr(runtime, "cancellation", None) or _default_cancellation


def request_cancel(mission_id: str, runtime: SwarmRuntime | None = None) -> None:
    _default_cancellation.request(mission_id)
    backend = _cancellation(runtime)
    if backend is not _default_cancellation:
        backend.request(mission_id)


def is_cancel_requested(mission_id: str, runtime: SwarmRuntime | None = None) -> bool:
    if _default_cancellation.is_requested(mission_id):
        return True
    backend = _cancellation(runtime)
    if backend is not _default_cancellation and backend.is_requested(mission_id):
        return True
    if runtime is None:
        return False
    raw = getattr(runtime.store, "get_raw", lambda _m: None)(mission_id)
    if isinstance(raw, dict) and (
        raw.get("status") == "cancelled" or raw.get("cancel_requested") is True
    ):
        return True
    got = runtime.store.get(mission_id)
    return bool(got is not None and got.status == "cancelled")


def clear_cancel(mission_id: str, runtime: SwarmRuntime | None = None) -> None:
    _default_cancellation.clear(mission_id)
    backend = _cancellation(runtime)
    if backend is not _default_cancellation:
        backend.clear(mission_id)


def new_mission_id(*, swarm_likely: bool = True) -> str:
    prefix = "MS" if swarm_likely else "M"
    return f"{prefix}-{uuid4().hex[:10]}"


def seed_running_mission(
    runtime: SwarmRuntime,
    *,
    mission_id: str,
    query: str,
    request_id: str,
    session_id: str,
    workspace_id: str | None = None,
    owner_user_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Persist a pollable running placeholder before background execution starts."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw: dict[str, Any] = {
        "route": "pending",
        "mission_id": mission_id,
        "status": "running",
        "query": query,
        "async": True,
        "trace": {"request_id": request_id, "session_id": session_id},
        "workspace_id": workspace_id,
        "owner_user_id": owner_user_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "artifacts": {
            "evidence": [],
            "anomaly": [],
            "hypothesis": [],
            "causal": [],
            "prediction": [],
            "strategy": [],
            "skeptic": [],
        },
        "events": [
            {
                "kind": "mission_accepted",
                "ts": ts,
                "seq": 1,
                "mission_id": mission_id,
                "family": "mission",
                "async": True,
            }
        ],
        "limitations": ["Mission accepted; execution in progress. Poll GET /v1/missions/{id}."],
        "final_response": None,
        "error_code": None,
    }
    result = MissionResult(
        mission_id=mission_id,
        status="running",
        limitations=list(raw["limitations"]),
        final_response=None,
        trace=TraceInfo(request_id=request_id, session_id=session_id),
    )
    runtime.store.put(result, raw)
    return raw


async def enqueue_durable_mission(
    runtime: SwarmRuntime,
    *,
    mission_id: str,
    query: str,
    timezone: str,
    as_of: str | None,
    session_id: str,
    request_id: str,
    workspace_id: str,
    owner_user_id: str,
    full_diagnostic: bool,
    full_prediction: bool,
    full_skeptic: bool,
    full_strategy: bool,
    execution_mode: str,
    schedule: bool = True,
) -> dict[str, Any]:
    """Persist a standalone API mission on the same durable run queue as conversations."""
    repositories = runtime.conversations
    queue = runtime.run_queue
    if repositories is None or queue is None:
        raise RuntimeError("durable mission queue is not configured")
    thread = Thread(
        id=f"thread_mission_{mission_id}",
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        title=query[:80],
        metadata={"system_hidden": True, "session_id": session_id},
    )
    run = Run(
        thread_id=thread.id,
        workspace_id=workspace_id,
        requested_by_user_id=owner_user_id,
        mission_id=mission_id,
        current_attempt=1,
        max_attempts=runtime.settings.run_max_attempts,
    )
    user_message = Message(
        id=f"message_mission_{mission_id}_user",
        thread_id=thread.id,
        workspace_id=workspace_id,
        user_id=owner_user_id,
        role=MessageRole.USER,
        run_id=run.id,
        parts=[MessagePart(type=MessagePartType.TEXT, content=query)],
    )
    assistant_message = Message(
        id=f"message_mission_{mission_id}_assistant",
        thread_id=thread.id,
        workspace_id=workspace_id,
        role=MessageRole.ASSISTANT,
        run_id=run.id,
        parent_message_id=user_message.id,
        parts=[
            MessagePart(
                type=MessagePartType.AGENT_STATUS,
                content="Working…",
                metadata={"status": "pending"},
            )
        ],
    )
    run = run.model_copy(
        update={
            "metadata": {
                "standalone_api": True,
                "submission": {
                    "query": query,
                    "timezone": timezone,
                    "as_of": as_of,
                    "request_id": request_id,
                    "execution_mode": execution_mode,
                    "assistant_message_id": assistant_message.id,
                    "full_diagnostic": full_diagnostic,
                    "full_prediction": full_prediction,
                    "full_skeptic": full_skeptic,
                    "full_strategy": full_strategy,
                },
            }
        }
    )
    with repositories.transaction() as writes:
        writes.threads.create(thread)
        writes.runs.create(run)
        writes.runs.add_attempt(
            RunAttempt(
                run_id=run.id,
                attempt_number=1,
                status=RunAttemptStatus.RETRYABLE,
            )
        )
        writes.messages.create(user_message)
        writes.messages.create(assistant_message)
        writes.runs.add_outbox(run.id)
    accepted = seed_running_mission(
        runtime,
        mission_id=mission_id,
        query=query,
        request_id=request_id,
        session_id=session_id,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        thread_id=thread.id,
        run_id=run.id,
    )
    if schedule:
        await publish_durable_mission(runtime, run.id)
    return accepted


async def publish_durable_mission(runtime: SwarmRuntime, run_id: str) -> None:
    """Best-effort queue notification; the committed outbox remains authoritative."""
    repositories = runtime.conversations
    queue = runtime.run_queue
    if repositories is None or queue is None:
        return
    try:
        await queue.enqueue(run_id)
        flush = getattr(queue, "flush", None)
        if callable(flush):
            await flush()
    except Exception:
        _log.warning("durable_mission_queue_notify_failed", exc_info=True)
    else:
        repositories.runs.mark_outbox_published(run_id)


async def run_mission_job(
    runtime: SwarmRuntime,
    *,
    mission_id: str,
    query: str,
    timezone: str,
    as_of: str | None,
    session_id: str | None,
    request_id: str,
    full_diagnostic: bool,
    full_prediction: bool,
    full_skeptic: bool,
    full_strategy: bool,
    execution_mode: str,
    context_bundle: dict | None = None,
) -> None:
    """Background worker: execute mission and overwrite the running placeholder."""
    seeded = getattr(runtime.store, "get_raw", lambda _m: None)(mission_id)
    ownership = {
        key: seeded.get(key)
        for key in ("workspace_id", "owner_user_id", "thread_id", "run_id")
        if isinstance(seeded, dict) and seeded.get(key) is not None
    }
    if is_cancel_requested(mission_id, runtime):
        _log.info("async_mission_skipped_cancelled", extra={"mission_id": mission_id})
        return
    try:
        async with asyncio.timeout(runtime.settings.mission_timeout_s):
            execute = (
                run_v3_mission
                if getattr(runtime.settings, "v3_agent_enabled", False)
                else run_any_mission
            )
            dispatched = await execute(
                runtime,
                query=query,
                timezone=timezone,
                as_of=as_of,
                session_id=session_id,
                request_id=request_id,
                mission_id=mission_id,
                full_diagnostic=full_diagnostic,
                full_prediction=full_prediction,
                full_skeptic=full_skeptic,
                full_strategy=full_strategy,
                execution_mode=execution_mode,
                context_bundle=context_bundle,
                workspace_id=ownership.get("workspace_id"),
                owner_user_id=ownership.get("owner_user_id"),
                thread_id=ownership.get("thread_id") or session_id,
                run_id=ownership.get("run_id") or request_id,
            )
        if is_cancel_requested(mission_id, runtime):
            # Cancel won — store.put refuses overwrite of cancelled; restore if needed.
            _log.info("async_mission_discarded_after_cancel", extra={"mission_id": mission_id})
            raw = getattr(runtime.store, "get_raw", lambda _m: None)(mission_id)
            if not (isinstance(raw, dict) and raw.get("status") == "cancelled"):
                cancel_running_mission(runtime, mission_id=mission_id, request_id=request_id)
            clear_cancel(mission_id, runtime)
            return
        # run_* already persists; ensure async marker survives on raw
        raw = getattr(runtime.store, "get_raw", lambda _m: None)(mission_id)
        if isinstance(raw, dict) and raw.get("status") != "cancelled":
            raw = {
                **raw,
                **ownership,
                "async": True,
                "route": dispatched.get("route") or raw.get("route"),
            }
            got = runtime.store.get(mission_id)
            if got is not None and got.status != "cancelled":
                runtime.store.put(got, raw)
        clear_cancel(mission_id, runtime)
    except Exception as exc:  # never leave a hung running mission
        if is_cancel_requested(mission_id, runtime):
            clear_cancel(mission_id, runtime)
            return
        _log.exception("async_mission_failed", extra={"mission_id": mission_id})
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        timed_out = isinstance(exc, TimeoutError)
        error_code = "MISSION_TIMEOUT" if timed_out else "ASYNC_EXECUTION_FAILED"
        detail = (
            f"Mission exceeded its {runtime.settings.mission_timeout_s:g}s timeout."
            if timed_out
            else f"Async mission failed: {type(exc).__name__}: {exc}"
        )
        fail = MissionResult(
            mission_id=mission_id,
            status="failed",
            limitations=[detail],
            final_response=None,
            trace=TraceInfo(request_id=request_id, session_id=session_id or request_id),
        )
        runtime.store.put(
            fail,
            {
                "route": "failed",
                "mission_id": mission_id,
                "status": "failed",
                "query": query,
                "async": True,
                **ownership,
                "error_code": error_code,
                "error_message": str(exc),
                "events": [
                    {
                        "kind": "mission_failed",
                        "ts": ts,
                        "seq": 2,
                        "mission_id": mission_id,
                        "family": "mission",
                        "error": str(exc),
                    }
                ],
                "limitations": fail.limitations,
            },
        )
        clear_cancel(mission_id, runtime)


def cancel_running_mission(
    runtime: SwarmRuntime,
    *,
    mission_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Mark a running async mission cancelled (cooperative — best-effort)."""
    raw = getattr(runtime.store, "get_raw", lambda _m: None)(mission_id)
    result = runtime.store.get(mission_id)
    if raw is None and result is None:
        raise KeyError(mission_id)

    status = None
    if isinstance(raw, dict):
        status = raw.get("status")
    if status is None and result is not None:
        status = result.status

    if str(status or "") != "running":
        raise ValueError(f"mission not cancellable (status={status})")

    request_cancel(mission_id, runtime)
    try:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Re-check immediately before write (job may have just finished).
        raw = getattr(runtime.store, "get_raw", lambda _m: None)(mission_id)
        result = runtime.store.get(mission_id)
        status = None
        if isinstance(raw, dict):
            status = raw.get("status")
        if status is None and result is not None:
            status = result.status
        if str(status or "") != "running":
            raise ValueError(f"mission not cancellable (status={status})")

        events = list((raw or {}).get("events") or [])
        events.append(
            {
                "kind": "mission_cancelled",
                "ts": ts,
                "seq": len(events) + 1,
                "mission_id": mission_id,
                "family": "mission",
                "async": True,
            }
        )
        rid = request_id or (result.trace.request_id if result and result.trace else uuid4().hex)
        sid = result.trace.session_id if result and result.trace else rid
        cancelled = MissionResult(
            mission_id=mission_id,
            status="cancelled",
            limitations=["Mission cancelled by client before completion."],
            final_response=None,
            trace=TraceInfo(request_id=rid, session_id=sid),
        )
        payload = {
            **(raw or {}),
            "route": (raw or {}).get("route") or "pending",
            "mission_id": mission_id,
            "status": "cancelled",
            "async": True,
            "cancel_requested": True,
            "events": events,
            "limitations": cancelled.limitations,
            "error_code": "CANCELLED",
            "final_response": None,
        }
        runtime.store.put(cancelled, payload)
        # If the job won the race, store refused the cancel write — surface that.
        got = runtime.store.get(mission_id)
        got_raw = getattr(runtime.store, "get_raw", lambda _m: None)(mission_id)
        final_status = None
        if isinstance(got_raw, dict):
            final_status = got_raw.get("status")
        if final_status is None and got is not None:
            final_status = got.status
        if final_status != "cancelled":
            raise ValueError(f"mission not cancellable (status={final_status})")
        return payload
    except Exception:
        # Do not leave a sticky cancel flag after a 409 / race loss.
        clear_cancel(mission_id, runtime)
        raise


# is_terminal_status imported from api.status and re-exported above

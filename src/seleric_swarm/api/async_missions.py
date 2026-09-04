"""Async mission acceptance — seed running placeholder, finish in background."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from seleric_swarm.api.status import TERMINAL_STATUSES, is_terminal_status
from seleric_swarm.contracts.lookup import MissionResult, TraceInfo
from seleric_swarm.orchestration.dispatch import run_any_mission
from seleric_swarm.runtime import SwarmRuntime

_log = logging.getLogger("seleric.api.async_missions")

# Re-export for callers / tests
_TERMINAL = TERMINAL_STATUSES

# Cooperative cancel flags for async background jobs (per process).
_cancel_requested: dict[str, bool] = {}


def request_cancel(mission_id: str) -> None:
    _cancel_requested[mission_id] = True


def is_cancel_requested(mission_id: str, runtime: SwarmRuntime | None = None) -> bool:
    if bool(_cancel_requested.get(mission_id)):
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


def clear_cancel(mission_id: str) -> None:
    _cancel_requested.pop(mission_id, None)


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
) -> dict[str, Any]:
    """Persist a pollable running placeholder before background execution starts."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw: dict[str, Any] = {
        "route": "pending",
        "mission_id": mission_id,
        "status": "running",
        "query": query,
        "async": True,
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
    scenario_id: str,
    execution_mode: str,
) -> None:
    """Background worker: execute mission and overwrite the running placeholder."""
    if is_cancel_requested(mission_id, runtime):
        _log.info("async_mission_skipped_cancelled", extra={"mission_id": mission_id})
        return
    try:
        dispatched = await run_any_mission(
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
            scenario_id=scenario_id,
            execution_mode=execution_mode,
        )
        if is_cancel_requested(mission_id, runtime):
            # Cancel won — store.put refuses overwrite of cancelled; restore if needed.
            _log.info("async_mission_discarded_after_cancel", extra={"mission_id": mission_id})
            raw = getattr(runtime.store, "get_raw", lambda _m: None)(mission_id)
            if not (isinstance(raw, dict) and raw.get("status") == "cancelled"):
                cancel_running_mission(runtime, mission_id=mission_id, request_id=request_id)
            clear_cancel(mission_id)
            return
        # run_* already persists; ensure async marker survives on raw
        raw = getattr(runtime.store, "get_raw", lambda _m: None)(mission_id)
        if isinstance(raw, dict) and raw.get("status") != "cancelled":
            raw = {**raw, "async": True, "route": dispatched.get("route") or raw.get("route")}
            got = runtime.store.get(mission_id)
            if got is not None and got.status != "cancelled":
                runtime.store.put(got, raw)
        clear_cancel(mission_id)
    except Exception as exc:  # never leave a hung running mission
        if is_cancel_requested(mission_id, runtime):
            clear_cancel(mission_id)
            return
        _log.exception("async_mission_failed", extra={"mission_id": mission_id})
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        fail = MissionResult(
            mission_id=mission_id,
            status="failed",
            limitations=[f"Async mission failed: {type(exc).__name__}: {exc}"],
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
                "error_code": "ASYNC_EXECUTION_FAILED",
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
        clear_cancel(mission_id)


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

    request_cancel(mission_id)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    return payload


# is_terminal_status imported from api.status and re-exported above

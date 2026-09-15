"""Terminal-visible mission flow logging.

Control-plane events and graph steps already exist for API/LangSmith consumers;
this module mirrors them to stdout so local `run_dev.py` shows the backend path
while a request is in flight (not only the final uvicorn access line).
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("seleric.mission.flow")

_ENVELOPE = frozenset(
    {
        "kind",
        "ts",
        "seq",
        "mission_id",
        "workflow_name",
        "workflow_version",
        "family",
        "legacy_kind",
    }
)


def _compact(value: Any, *, limit: int = 160) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else f"{value[: limit - 3]}..."
    if isinstance(value, dict):
        return f"<dict n={len(value)}>"
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__} n={len(value)}>"
    return value


def log_mission_event(event: dict[str, Any]) -> None:
    """Print one mission-flow line and emit a structured log record."""
    kind = str(event.get("kind") or "unknown")
    mid = event.get("mission_id") or "-"
    seq = event.get("seq")
    extras = {k: _compact(v) for k, v in event.items() if k not in _ENVELOPE and v is not None}
    _log.info("mission.flow", mission_id=mid, seq=seq, kind=kind, **extras)
    bits = " ".join(f"{k}={v!r}" for k, v in list(extras.items())[:8])
    prefix = f"[mission {mid}"
    if seq is not None:
        prefix += f" #{seq}"
    prefix += f"] {kind}"
    print(f"{prefix} {bits}".rstrip(), flush=True)


def log_mission_step(mission_id: str | None, step: str, **data: Any) -> None:
    """Ad-hoc step outside the event emitter (classify, route, start/end)."""
    payload = {"kind": step, "mission_id": mission_id, **data}
    log_mission_event(payload)

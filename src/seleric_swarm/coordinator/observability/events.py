"""Structured mission-control events for swarm observability.

Canonical kinds match docs/coordinator/14_OBSERVABILITY.md. Every event carries
a stable envelope (kind, ts, seq, mission_id) so API consumers and LangSmith
can join without ad-hoc parsing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

MissionEventObserver = Callable[[dict[str, Any]], None]
_event_observer: ContextVar[MissionEventObserver | None] = ContextVar(
    "seleric_mission_event_observer", default=None
)


@contextmanager
def observe_mission_events(observer: MissionEventObserver) -> Iterator[None]:
    """Observe mission events emitted in this async context."""
    token = _event_observer.set(observer)
    try:
        yield
    finally:
        _event_observer.reset(token)


def notify_mission_event(event: dict[str, Any]) -> None:
    """Notify the active observer without changing execution semantics."""
    observer = _event_observer.get()
    if observer is not None:
        try:
            observer(dict(event))
        except Exception:
            return


# Prefix families required by the observability contract.
EVENT_FAMILIES = (
    "mission_",
    "decomposition_",
    "task_",
    "artifact_",
    "leadership_",
    "claim_",
    "skeptic_",
    "remediation_",
    "specialist_",
)

# Well-known kinds emitted by the control plane.
MISSION_CREATED = "mission_created"
MISSION_COMPLETED = "mission_completed"
MISSION_PARTIAL = "mission_partial"
MISSION_FAILED = "mission_failed"
MISSION_BUDGET_EXHAUSTED = "mission_budget_exhausted"
MISSION_CONTROL_PLANE = "mission_control_plane"

DECOMPOSITION_CREATED = "decomposition_created"
DECOMPOSITION_REFINED = "decomposition_refined"

TASK_PLAN_CREATED = "task_plan_created"
TASK_WAVE_EXECUTED = "task_wave_executed"
TASK_SPECIALISTS_ACTIVATED = "task_specialists_activated"

LEADERSHIP_TRANSFER = "leadership_transfer"
LEADERSHIP_REJECTED = "leadership_rejected"

CLAIM_PROPOSED = "claim_proposed"
CLAIM_VALIDATED = "claim_validated"
CLAIM_CHALLENGED = "claim_challenged"
CLAIM_REJECTED = "claim_rejected"

SKEPTIC_GATE = "skeptic_gate"
SKEPTIC_PASS = "skeptic_pass"
SKEPTIC_REVISE = "skeptic_revise"
SKEPTIC_REJECT = "skeptic_reject"

REMEDIATION_PLANNED = "remediation_planned"
REMEDIATION_ACTIVATED = "remediation_activated"
REMEDIATION_ROUND_DONE = "remediation_round_done"

SPECIALIST_ERROR = "specialist_error"
SPECIALIST_SKIPPED_POLICY = "specialist_skipped_policy"

# Specialist lifecycle events don't follow the ``specialist_`` prefix
# convention (they predate it: ``observed``, ``anomaly_done``, etc.) — without
# this, event_families rollups undercount every specialist run to zero.
_SPECIALIST_LIFECYCLE_KINDS = frozenset(
    {
        "observed",
        "observe_skipped",
        "anomaly_done",
        "diagnostic_done",
        "prediction_done",
        "strategy_done",
    }
)

# Backward-compatible aliases → canonical kind
_ALIASES: dict[str, str] = {
    "query_normalized": MISSION_CREATED,
    "plan_created": TASK_PLAN_CREATED,
    "decide_execute_wave": TASK_WAVE_EXECUTED,
    "budget_exhausted": MISSION_BUDGET_EXHAUSTED,
    "handoff_rejected": LEADERSHIP_REJECTED,
    "skeptic_revise_remediation": REMEDIATION_PLANNED,
    "remediation_activate": REMEDIATION_ACTIVATED,
}


def canonical_kind(kind: str) -> str:
    return _ALIASES.get(kind, kind)


def family_of(kind: str) -> str | None:
    if kind in _SPECIALIST_LIFECYCLE_KINDS:
        return "specialist"
    for prefix in EVENT_FAMILIES:
        if kind.startswith(prefix):
            return prefix.rstrip("_")
    return None


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

"""Structured mission-control events for swarm observability.

Canonical kinds match docs/coordinator/14_OBSERVABILITY.md. Every event carries
a stable envelope (kind, ts, seq, mission_id) so API consumers and LangSmith
can join without ad-hoc parsing.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from seleric_swarm.swarm.blackboard import Blackboard

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
)

# Well-known kinds emitted by the control plane.
MISSION_CREATED = "mission_created"
MISSION_COMPLETED = "mission_completed"
MISSION_PARTIAL = "mission_partial"
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
    for prefix in EVENT_FAMILIES:
        if kind.startswith(prefix):
            return prefix.rstrip("_")
    return None


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class MissionEventEmitter:
    """Appends envelope-normalized events onto a Blackboard."""

    def __init__(
        self,
        blackboard: Blackboard,
        *,
        workflow_name: str = "swarm_v2",
        workflow_version: str = "1.3.0",
    ) -> None:
        self.blackboard = blackboard
        self.workflow_name = workflow_name
        self.workflow_version = workflow_version

    def emit(self, kind: str, **data: Any) -> dict[str, Any]:
        canon = canonical_kind(kind)
        payload = {k: v for k, v in data.items() if v is not None}
        if kind != canon:
            payload.setdefault("legacy_kind", kind)
        event = {
            "kind": canon,
            "ts": now_iso(),
            "seq": len(self.blackboard.events) + 1,
            "mission_id": self.blackboard.mission_id,
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "family": family_of(canon),
            **payload,
        }
        self.blackboard.events.append(event)
        return event

    def kinds(self) -> list[str]:
        return [e.get("kind") for e in self.blackboard.events if e.get("kind")]


def summarize_event_families(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {f.rstrip("_"): 0 for f in EVENT_FAMILIES}
    for event in events:
        fam = event.get("family") or family_of(str(event.get("kind") or ""))
        if fam in counts:
            counts[fam] += 1
    return counts


def assert_lifecycle_coverage(events: list[dict[str, Any]]) -> list[str]:
    """Return missing required families for a completed swarm_v2 mission."""
    present = summarize_event_families(events)
    required = ("mission", "decomposition", "task")
    return [name for name in required if present.get(name, 0) == 0]

"""Typed value objects for the coordinator control plane.

These are plain dataclasses so the planes stay deterministic and unit-testable
without an LLM or a live graph. ``TaskGraph`` serializes to the ``dict`` shape
that ``MissionState["task_graph"]`` already carries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

TaskStatus = str  # pending | ready | running | done | failed | blocked


class ComplexityLevel(IntEnum):
    """Mission complexity, mirroring docs/03 and the pasted architecture (L0-L5)."""

    L0 = 0  # direct deterministic retrieval (one metric, one day)
    L1 = 1  # single domain + observer (metric needs domain framing)
    L2 = 2  # comparison / change detection across periods or entities
    L3 = 3  # anomaly interpretation layered on a change
    L4 = 4  # diagnostic / causal reasoning
    L5 = 5  # cross-domain, dynamic-leadership investigation

    @property
    def label(self) -> str:
        return self.name


@dataclass
class Task:
    """A single unit of delegated work with explicit contracts (pasted spec sec. 11)."""

    id: str
    type: str
    objective: str
    required_capabilities: list[str] = field(default_factory=list)
    metric_ids: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    expected_artifacts: list[str] = field(default_factory=list)
    assigned_agent: str | None = None
    dispatchable: bool = False
    blocked_reason: str | None = None
    priority: int = 5
    max_retries: int = 2
    timeout_seconds: int = 60
    status: TaskStatus = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "objective": self.objective,
            "required_capabilities": list(self.required_capabilities),
            "metric_ids": list(self.metric_ids),
            "depends_on": list(self.depends_on),
            "inputs": list(self.inputs),
            "expected_artifacts": list(self.expected_artifacts),
            "assigned_agent": self.assigned_agent,
            "agent": self.assigned_agent,  # backward-compatible alias
            "dispatchable": self.dispatchable,
            "blocked_reason": self.blocked_reason,
            "priority": self.priority,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Task:
        return cls(
            id=str(payload["id"]),
            type=str(payload.get("type") or "observe"),
            objective=str(payload.get("objective") or ""),
            required_capabilities=list(payload.get("required_capabilities") or []),
            metric_ids=list(payload.get("metric_ids") or []),
            depends_on=list(payload.get("depends_on") or []),
            inputs=list(payload.get("inputs") or []),
            expected_artifacts=list(payload.get("expected_artifacts") or []),
            assigned_agent=payload.get("assigned_agent") or payload.get("agent"),
            dispatchable=bool(payload.get("dispatchable", False)),
            blocked_reason=payload.get("blocked_reason"),
            priority=int(payload.get("priority", 5)),
            max_retries=int(payload.get("max_retries", 2)),
            timeout_seconds=int(payload.get("timeout_seconds", 60)),
            status=str(payload.get("status") or "pending"),
        )


@dataclass
class TaskGraph:
    """A dependency DAG of tasks plus the mission complexity that produced it."""

    tasks: list[Task] = field(default_factory=list)
    complexity: ComplexityLevel = ComplexityLevel.L0
    notes: list[str] = field(default_factory=list)

    def get(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def ready(self) -> list[Task]:
        """Pending tasks whose dependencies are all ``done``."""

        done = {t.id for t in self.tasks if t.status == "done"}
        out: list[Task] = []
        for task in self.tasks:
            if task.status != "pending":
                continue
            if all(dep in done for dep in task.depends_on):
                out.append(task)
        return sorted(out, key=lambda t: (-t.priority, t.id))

    @property
    def dispatchable(self) -> bool:
        return bool(self.tasks) and all(t.dispatchable for t in self.tasks)

    @property
    def blocked_tasks(self) -> list[Task]:
        return [t for t in self.tasks if not t.dispatchable]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "complexity": int(self.complexity),
            "complexity_label": self.complexity.label,
            "dispatchable": self.dispatchable,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> TaskGraph:
        payload = payload or {}
        level = payload.get("complexity", 0)
        try:
            complexity = ComplexityLevel(int(level))
        except (ValueError, TypeError):
            complexity = ComplexityLevel.L0
        return cls(
            tasks=[Task.from_dict(t) for t in payload.get("tasks") or []],
            complexity=complexity,
            notes=list(payload.get("notes") or []),
        )

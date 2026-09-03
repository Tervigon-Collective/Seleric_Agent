"""Evidence gap detector (pasted spec sec. 29).

After each wave the coordinator asks: can current evidence answer the mission?
This module only reports gaps it can actually close - a gap whose capability or
data path is not live is a limitation, not a task - which keeps the coordinator
from manufacturing unreachable work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from seleric_swarm.coordinator.models import Task
from seleric_swarm.coordinator.routing.dispatchability import DispatchGuard


@dataclass
class EvidenceGap:
    metric_id: str | None
    required_capability: str
    rationale: str
    dispatchable: bool
    suggested_task: Task | None = None


def detect_gaps(
    *,
    state: dict[str, Any],
    guard: DispatchGuard,
) -> list[EvidenceGap]:
    gaps: list[EvidenceGap] = []
    observed = {row.get("metric_or_fact") for row in state.get("evidence") or []}

    # Owed metrics the current lead cannot serve (drives leadership transfer).
    for metric_id in state.get("handoff_needed_metrics") or []:
        if metric_id in observed:
            continue
        probe = Task(
            id=f"T-gap-{metric_id.split('.')[-1]}",
            type="observe_metric",
            objective=f"Retrieve {metric_id} for the mission time range",
            required_capabilities=["metric_observation", "evidence_collection"],
            metric_ids=[metric_id],
            assigned_agent="observer_agent",
        )
        verdict = guard.check(probe)
        probe.dispatchable = verdict.dispatchable
        probe.blocked_reason = verdict.reason
        gaps.append(
            EvidenceGap(
                metric_id=metric_id,
                required_capability="metric_observation",
                rationale=(
                    f"{metric_id} is required by the question but not yet in evidence"
                    if verdict.dispatchable
                    else f"{metric_id} is needed but unreachable: {verdict.reason}"
                ),
                dispatchable=verdict.dispatchable,
                suggested_task=probe if verdict.dispatchable else None,
            )
        )

    return gaps

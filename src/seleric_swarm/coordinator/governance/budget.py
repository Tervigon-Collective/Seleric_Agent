"""Budget and hard-stop controller (pasted spec sec. 33-34).

Disabled per explicit request: ``check_budget``/``check_hard_stops``/
``check_swarm_budget`` are no-ops that always report ok, so nothing in the
system rejects or truncates a mission for LLM/tool/agent-call/runtime spend
any more. Call sites (``coordinator.plane.ControlPlane``, ``orchestration
.graph``, ``coordinator.graph``) are unchanged and still call these functions,
they just never see a non-ok verdict now. Re-enabling is a low-priority
backlog item -- see docs/TASK_SHEET.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from seleric_swarm.coordinator.contracts import MissionBudget


@dataclass(frozen=True)
class MissionLimits:
    max_llm_calls: int
    max_tool_calls: int
    max_agent_calls: int = 30
    max_leadership_transfers: int = 6
    max_iterations: int = 12
    max_runtime_seconds: float = 120.0

    @classmethod
    def from_settings(cls, settings: Any) -> MissionLimits:
        return cls(
            max_llm_calls=int(getattr(settings, "max_llm_calls", 6)),
            max_tool_calls=int(getattr(settings, "max_tool_calls", 8)),
            max_agent_calls=int(getattr(settings, "max_agent_calls", 30)),
            max_leadership_transfers=int(getattr(settings, "max_leadership_transfers", 6)),
            max_iterations=int(getattr(settings, "max_coordinator_iterations", 12)),
            max_runtime_seconds=float(getattr(settings, "mission_timeout_s", 120.0)),
        )


@dataclass
class BudgetVerdict:
    ok: bool
    error_code: str | None = None
    reason: str | None = None
    exhausted_key: str | None = None


_OK = BudgetVerdict(ok=True)


def check_budget(
    state: dict[str, Any],
    limits: MissionLimits,
    *,
    llm_needed: int = 0,
    tool_needed: int = 0,
) -> BudgetVerdict:
    return _OK


def check_hard_stops(state: dict[str, Any], limits: MissionLimits) -> BudgetVerdict:
    return _OK


def check_swarm_budget(
    state: dict[str, Any],
    budgets: MissionBudget | dict[str, Any],
    *,
    agent_calls_needed: int = 0,
    token_usage: int = 0,
) -> BudgetVerdict:
    """Hard-stop check for swarm_v2 against MissionBudget / policy ceilings.

    Returns ok=False when any ceiling is already met or would be crossed by
    ``agent_calls_needed``. Used to stop investigate loops and force partial
    completion instead of unbounded DECIDE→EXECUTE cycling.

    Investigate-wave ceilings stay in the refine router (leadership.max_transfers).
    """
    return _OK

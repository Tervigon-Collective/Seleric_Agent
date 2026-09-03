"""Budget and hard-stop controller (pasted spec sec. 33-34).

Deterministic ceilings the coordinator enforces before dispatching any step, so
intelligence cannot run forever. The LLM/tool ceilings reproduce the exact
semantics of the previous ``graph._budget_ok`` helper; the rest are new hard
limits (iterations, leadership transfers, agent calls) for the DECIDE -> EXECUTE
cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


_OK = BudgetVerdict(ok=True)


def check_budget(
    state: dict[str, Any],
    limits: MissionLimits,
    *,
    llm_needed: int = 0,
    tool_needed: int = 0,
) -> BudgetVerdict:
    if int(state.get("llm_calls") or 0) + llm_needed > limits.max_llm_calls:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "LLM call budget exceeded")
    if int(state.get("tool_calls") or 0) + tool_needed > limits.max_tool_calls:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "Tool call budget exceeded")
    return _OK


def check_hard_stops(state: dict[str, Any], limits: MissionLimits) -> BudgetVerdict:
    if int(state.get("coordinator_iterations") or 0) > limits.max_iterations:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "Coordinator iteration ceiling reached")
    if len(state.get("handoff_history") or []) > limits.max_leadership_transfers:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "Leadership transfer ceiling reached")
    if int(state.get("agent_calls") or 0) > limits.max_agent_calls:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "Agent call ceiling reached")
    return _OK

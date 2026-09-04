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
    if int(state.get("llm_calls") or 0) + llm_needed > limits.max_llm_calls:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "LLM call budget exceeded", "llm_calls")
    if int(state.get("tool_calls") or 0) + tool_needed > limits.max_tool_calls:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "Tool call budget exceeded", "tool_calls")
    return _OK


def check_hard_stops(state: dict[str, Any], limits: MissionLimits) -> BudgetVerdict:
    if int(state.get("coordinator_iterations") or 0) > limits.max_iterations:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "Coordinator iteration ceiling reached", "iterations")
    if len(state.get("handoff_history") or []) > limits.max_leadership_transfers:
        return BudgetVerdict(
            False, "BUDGET_EXCEEDED", "Leadership transfer ceiling reached", "leadership_transfers"
        )
    if int(state.get("agent_calls") or 0) > limits.max_agent_calls:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "Agent call ceiling reached", "agent_calls")
    return _OK


def _as_mission_budget(budgets: MissionBudget | dict[str, Any]) -> MissionBudget:
    if isinstance(budgets, MissionBudget):
        return budgets
    return MissionBudget(**{k: v for k, v in budgets.items() if k in MissionBudget.model_fields})


def check_swarm_budget(
    state: dict[str, Any],
    budgets: MissionBudget | dict[str, Any],
    *,
    agent_calls_needed: int = 0,
) -> BudgetVerdict:
    """Hard-stop check for swarm_v2 against MissionBudget / policy ceilings.

    Returns ok=False when any ceiling is already met or would be crossed by
    ``agent_calls_needed``. Used to stop investigate loops and force partial
    completion instead of unbounded DECIDE→EXECUTE cycling.

    Investigate-wave ceilings stay in the refine router (leadership.max_transfers).
    """
    budgets = _as_mission_budget(budgets)
    usage = dict(state.get("usage") or {})
    agent_calls = int(usage.get("agent_calls") or state.get("agent_calls") or 0)

    if agent_calls_needed > 0 and agent_calls + agent_calls_needed > budgets.max_agent_calls:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "Agent call budget exhausted", "agent_calls")
    if agent_calls >= budgets.max_agent_calls:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "Agent call budget exhausted", "agent_calls")

    transfers = len(state.get("handoff_history") or [])
    if transfers >= budgets.max_leadership_transfers:
        return BudgetVerdict(
            False, "BUDGET_EXCEEDED", "Leadership transfer ceiling reached", "leadership_transfers"
        )

    rem_round = int(usage.get("remediation_rounds") or state.get("remediation_round") or 0)
    if rem_round >= budgets.max_remediation_rounds:
        return BudgetVerdict(
            False, "BUDGET_EXCEEDED", "Remediation round budget exhausted", "remediation_rounds"
        )

    llm_calls = int(usage.get("llm_calls") or state.get("llm_calls") or 0)
    if llm_calls >= budgets.max_llm_calls:
        return BudgetVerdict(False, "BUDGET_EXCEEDED", "LLM call budget exhausted", "llm_calls")

    return _OK

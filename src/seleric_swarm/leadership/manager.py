from __future__ import annotations
from typing import Any


class LeadershipManager:
    def should_arbitrate(self, history: list[dict[str, Any]], proposal: dict[str, Any]) -> bool:
        if not proposal.get("evidence_refs"):
            return True
        recent = [(h.get("from_agent"), h.get("to_agent")) for h in history[-4:]]
        # Simple ping-pong detector placeholder.
        if len(recent) >= 4 and recent[-1] == recent[-3] and recent[-2] == recent[-4]:
            return True
        return False

    def apply(self, state: dict[str, Any], to_agent: str, transfer: dict[str, Any]) -> dict[str, Any]:
        state["mission_lead"] = to_agent
        state["leadership_epoch"] = int(state.get("leadership_epoch", 0)) + 1
        state.setdefault("handoff_history", []).append(transfer)
        return state

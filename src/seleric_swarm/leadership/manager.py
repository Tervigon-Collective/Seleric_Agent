from __future__ import annotations

from typing import Any

from seleric_swarm.domain.models import LeadershipTransfer


class LeadershipManager:
    def should_arbitrate(self, history: list[dict[str, Any]], proposal: dict[str, Any]) -> bool:
        if not proposal.get("evidence_refs"):
            return True
        recent = [(h.get("from_agent"), h.get("to_agent")) for h in history[-4:]]
        return len(recent) >= 4 and recent[-1] == recent[-3] and recent[-2] == recent[-4]

    def apply(self, state: dict[str, Any], to_agent: str, transfer: dict[str, Any]) -> dict[str, Any]:
        epoch = int(state.get("leadership_epoch", 0)) + 1
        record = {
            "mission_id": state.get("mission_id") or transfer.get("mission_id"),
            "from_agent": transfer.get("from_agent"),
            "to_agent": to_agent,
            "requested_target": transfer.get("requested_target") or to_agent,
            "reason": transfer.get("reason"),
            "evidence_refs": list(transfer.get("evidence_refs") or []),
            "unresolved_question": transfer.get("unresolved_question"),
            "requested_output": transfer.get("requested_output"),
            "epoch": epoch,
        }
        state["mission_lead"] = to_agent
        state["leadership_epoch"] = epoch
        state.setdefault("handoff_history", []).append(record)
        return state

    def decide(self, state: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
        """Accept a schema-valid, evidenced transfer; otherwise reject without mutating lead."""
        history = list(state.get("handoff_history") or [])
        if self.should_arbitrate(history, proposal):
            reason = (
                "Leadership transfer requires evidence references"
                if not proposal.get("evidence_refs")
                else "Coordinator rejected transfer (loop or policy)"
            )
            return {
                "accepted": False,
                "error_code": "HANDOFF_REJECTED",
                "error_message": reason,
            }
        payload = LeadershipTransfer(
            mission_id=str(state.get("mission_id") or proposal.get("mission_id") or ""),
            from_agent=str(proposal.get("from_agent") or ""),
            requested_target=str(proposal.get("requested_target") or proposal.get("to_agent") or ""),
            reason=str(proposal.get("reason") or ""),
            evidence_refs=list(proposal.get("evidence_refs") or []),
            unresolved_question=str(proposal.get("unresolved_question") or ""),
            requested_output=proposal.get("requested_output"),
        )
        applied = self.apply(dict(state), payload.requested_target, payload.model_dump())
        return {
            "accepted": True,
            "mission_lead": applied["mission_lead"],
            "leadership_epoch": applied["leadership_epoch"],
            "handoff_history": applied["handoff_history"],
        }

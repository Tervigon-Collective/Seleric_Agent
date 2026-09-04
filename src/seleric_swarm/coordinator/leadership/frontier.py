"""Dynamic leadership — frontier, hysteresis, loop detection."""

from __future__ import annotations

from typing import Any

from seleric_swarm.coordinator.contracts import LeadershipTransferRequest
from seleric_swarm.coordinator.policies import CoordinatorPolicies, load_coordinator_policies
from seleric_swarm.leadership.manager import LeadershipManager


def detect_leadership_loop(history: list[dict[str, Any]], window: int = 4) -> bool:
    recent = [(h.get("from_agent"), h.get("to_agent")) for h in history[-window:]]
    if len(recent) >= 4 and recent[-1] == recent[-3] and recent[-2] == recent[-4]:
        return True
    # A→B→A→B pattern of length 4
    if len(recent) >= 4:
        a, b = recent[-4][0], recent[-4][1]
        if recent[-3] == (b, a) and recent[-2] == (a, b) and recent[-1] == (b, a):
            return True
    return False


def evaluate_frontier(
    *,
    anomalies: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    current_lead: str,
    topology: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Causal frontier = domain closest to the strongest unresolved driver."""
    topology = topology or {}
    pool = anomalies + evidence
    scores: dict[str, float] = {
        "performance": 0.0,
        "funnel": 0.0,
        "technical": 0.0,
        "commerce": 0.0,
        "finance": 0.0,
    }
    for a in pool:
        mid = str(a.get("metric_id") or a.get("metric_or_fact") or "").lower()
        dims = a.get("dimensions") or {}
        dev = abs(float(a.get("deviation_pct") or a.get("change_pct") or 0))
        if any(k in mid for k in ("cpm", "cpc", "ctr", "cac", "spend", "roas")):
            scores["performance"] += dev
        if any(k in mid for k in ("cvr", "conversion", "checkout", "session", "atc", "pdp")):
            scores["funnel"] += dev
        if dims.get("device") == "mobile" or any(k in mid for k in ("lcp", "js_error", "latency", "mobile")):
            scores["technical"] += dev * 1.2
        if any(k in mid for k in ("sales", "orders", "revenue")):
            scores["commerce"] += dev

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best_domain, best_score = ranked[0]
    lead_domain = current_lead.removesuffix("_agent")
    proposed = f"{best_domain}_agent"
    return {
        "frontier_domain": best_domain,
        "frontier_score": best_score,
        "proposed_lead": proposed,
        "current_lead_domain": lead_domain,
        "should_transfer": best_domain != lead_domain and best_score >= 10,
        "ranking": ranked,
        "topology_hint": (topology.get(lead_domain) or {}),
    }


class LeadershipController:
    def __init__(
        self,
        manager: LeadershipManager | None = None,
        policies: CoordinatorPolicies | None = None,
    ) -> None:
        self.manager = manager or LeadershipManager()
        self.policies = policies or load_coordinator_policies()

    def decide_transfer(
        self,
        state: dict[str, Any],
        request: LeadershipTransferRequest | dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(request, LeadershipTransferRequest):
            proposal = request.model_dump()
        else:
            proposal = dict(request)

        history = list(state.get("handoff_history") or [])
        if detect_leadership_loop(history, self.policies.leadership.loop_window):
            return {
                "accepted": False,
                "error_code": "LEADERSHIP_LOOP",
                "error_message": "Leadership ping-pong detected; arbitration required",
            }
        if self.policies.leadership.require_new_evidence and not proposal.get("evidence_refs"):
            return {
                "accepted": False,
                "error_code": "HANDOFF_REJECTED",
                "error_message": "Leadership transfer requires evidence references",
            }
        # hysteresis: reject transferring back to a recent target
        from seleric_swarm.coordinator.leadership.hysteresis import recent_target_blocked

        target = str(proposal.get("requested_target") or "")
        if recent_target_blocked(history, target, window=2):
            return {
                "accepted": False,
                "error_code": "HYSTERESIS",
                "error_message": "Recent transfer to same target (hysteresis)",
            }
        return self.manager.decide(state, proposal)

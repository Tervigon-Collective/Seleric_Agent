from seleric_swarm.leadership.manager import LeadershipManager


def test_transfer_without_evidence_requires_arbitration():
    mgr = LeadershipManager()
    assert mgr.should_arbitrate([], {"evidence_refs": []})


def test_decide_rejects_transfer_without_evidence():
    mgr = LeadershipManager()
    decision = mgr.decide(
        {"mission_id": "M-1", "mission_lead": "performance_agent", "leadership_epoch": 0},
        {
            "from_agent": "performance_agent",
            "to_agent": "commerce_agent",
            "reason": "Need sales",
            "evidence_refs": [],
            "unresolved_question": "Retrieve metric.net_sales",
        },
    )
    assert decision["accepted"] is False
    assert decision["error_code"] == "HANDOFF_REJECTED"


def test_decide_rejects_ping_pong():
    mgr = LeadershipManager()
    history = [
        {"from_agent": "performance_agent", "to_agent": "commerce_agent"},
        {"from_agent": "commerce_agent", "to_agent": "performance_agent"},
        {"from_agent": "performance_agent", "to_agent": "commerce_agent"},
        {"from_agent": "commerce_agent", "to_agent": "performance_agent"},
    ]
    decision = mgr.decide(
        {
            "mission_id": "M-1",
            "mission_lead": "performance_agent",
            "leadership_epoch": 4,
            "handoff_history": history,
        },
        {
            "from_agent": "performance_agent",
            "to_agent": "commerce_agent",
            "reason": "Need sales",
            "evidence_refs": ["EV-1"],
            "unresolved_question": "Retrieve metric.net_sales",
        },
    )
    assert decision["accepted"] is False
    assert decision["error_code"] == "HANDOFF_REJECTED"


def test_apply_increments_epoch():
    mgr = LeadershipManager()
    state = {"mission_lead": "performance_agent", "leadership_epoch": 1}
    out = mgr.apply(state, "commerce_agent", {"from_agent": "performance_agent", "to_agent": "commerce_agent"})
    assert out["mission_lead"] == "commerce_agent"
    assert out["leadership_epoch"] == 2

from seleric_swarm.leadership.manager import LeadershipManager


def test_transfer_without_evidence_requires_arbitration():
    mgr = LeadershipManager()
    assert mgr.should_arbitrate([], {"evidence_refs": []})


def test_apply_increments_epoch():
    mgr = LeadershipManager()
    state = {"mission_lead": "performance_agent", "leadership_epoch": 1}
    out = mgr.apply(state, "commerce_agent", {"from_agent": "performance_agent", "to_agent": "commerce_agent"})
    assert out["mission_lead"] == "commerce_agent"
    assert out["leadership_epoch"] == 2

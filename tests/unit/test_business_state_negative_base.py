from seleric_swarm.services.business_state.detectors import robust_zscore


def test_a_move_off_a_negative_base_keeps_its_direction_in_the_percent():
    r = robust_zscore([-100.0, -100.0, -100.0], 50.0)
    assert r.direction == "up"
    assert r.deviation_pct is not None and r.deviation_pct > 0

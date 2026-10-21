from seleric_swarm.services.business_state.detectors import robust_zscore


def test_a_move_off_a_negative_base_keeps_its_direction_in_the_percent():
    r = robust_zscore([-100.0, -100.0, -100.0], 50.0)
    assert r.direction == "up"
    assert r.deviation_pct is not None and r.deviation_pct > 0


def test_a_percent_off_a_near_zero_base_is_not_handed_to_the_model():
    # live 2026-10-15 "what are my top priority today": "refunds +5,851.7%" was reported as the top risk
    from seleric_swarm.services.business_state.formatter import _snapshot_context
    from seleric_swarm.services.domain_health.models import DomainStateSnapshot, ResolvedMetric

    snap = DomainStateSnapshot(
        domain="business", brand_id="20", as_of="2026-10-15", computed_at="2026-10-15T00:00:00+00:00", status="OK",
        metrics=[ResolvedMetric(metric_id="metric.refunds", value=9000.0, period_delta_pct=5851.7, rolling_mean_7d=800.0),
                 ResolvedMetric(metric_id="metric.net_sales", value=71727.93, period_delta_pct=-18.0)],
    )
    by_metric = {m["metric"]: m for m in _snapshot_context(snap)["metrics"]}
    assert by_metric["refunds"]["change_pct_vs_prior_day"] is None and "near zero" in by_metric["refunds"]["change_note"]
    assert by_metric["refunds"]["value"] == 9000.0 and by_metric["refunds"]["avg_7d"] == 800.0
    assert by_metric["net_sales"]["change_pct_vs_prior_day"] == -18.0 and "change_note" not in by_metric["net_sales"]


def test_a_change_off_a_near_zero_base_is_not_a_percent_in_a_diagnosis_statement():
    from seleric_swarm.causal.diagnosis import _chg

    assert _chg(5851.0, 1.0) == "n/a (near-zero base)"
    assert _chg(500.0, 100.0) == "+400%" and _chg(110.0, 100.0) == "+10.0%"

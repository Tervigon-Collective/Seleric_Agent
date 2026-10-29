from datetime import date, timedelta

from seleric_swarm.services.domain_health.judge import MIN_HISTORY, infer_shape, judge


def _series(values, end=date(2026, 10, 28)):
    start = end - timedelta(days=len(values) - 1)
    return [((start + timedelta(days=i)).isoformat(), v) for i, v in enumerate(values)]


def test_shape_comes_from_unit_and_data():
    assert infer_shape("ratio", [0.1, 0.2]) == "ratio"
    assert infer_shape("INR", [5.0, -2.0]) == "signed"
    assert infer_shape("count", [5.0, 7.0]) == "additive"


def test_negative_base_never_reports_a_percent():
    # Net profit around -1000 that falls to -5000: the first ready store printed "-448%".
    j = judge(_series([-1000.0 + (i % 3) * 40 for i in range(27)] + [-5000.0]), unit="INR", direction_bad="down")
    assert j.shape == "signed" and j.unit == "abs"
    assert j.delta_pct is None
    assert j.status == "alert" and j.worse_by > 3000


def test_good_direction_anomaly_is_not_an_alert():
    base = [0.010 + 0.0002 * (i % 4) for i in range(27)]
    j = judge(_series(base + [0.021]), unit="ratio", direction_bad="down")  # conversion doubled
    assert j.is_anomaly and j.direction == "up"
    assert j.status == "good"


def test_unknown_polarity_is_watched_not_alerted():
    j = judge(_series([100.0 + (i % 3) for i in range(27)] + [400.0]), unit="count", direction_bad=None)
    assert j.is_anomaly and j.status == "watch"


def test_band_never_goes_below_zero_for_non_negative_metrics():
    j = judge(_series([10.0, 80.0, 15.0, 90.0, 12.0, 70.0, 20.0, 85.0, 11.0, 75.0, 14.0, 88.0, 13.0, 60.0] * 2), unit="count", direction_bad="down")
    assert j.low >= 0


def test_weekday_baseline_ignores_normal_weekend_dip():
    # Sundays sell 40% less. A Sunday at the usual Sunday level must not alert.
    end = date(2026, 10, 25)  # Sunday
    days = [end - timedelta(days=i) for i in range(28)][::-1]
    values = [60.0 if d.weekday() == 6 else 100.0 for d in days]
    j = judge([(d.isoformat(), v) for d, v in zip(days, values)], unit="count", direction_bad="down")
    assert j.basis == "weekday" and j.status == "ok"


def test_flat_history_small_wobble_is_not_an_anomaly():
    j = judge(_series([100.0] * 27 + [101.0]), unit="count", direction_bad="down")
    assert not j.is_anomaly


def test_worse_by_rule_uses_pp_for_ratios_and_pct_for_counts():
    ratio = judge(_series([0.20 + 0.001 * (i % 3) for i in range(27)] + [0.17]), unit="ratio", direction_bad="down",
                  rule={"on": "worse_by", "threshold": 2})
    assert ratio.unit == "pp" and ratio.status == "alert"
    count = judge(_series([100.0 + (i % 5) for i in range(27)] + [93.0]), unit="count", direction_bad="down",
                  rule={"on": "worse_by", "threshold": 10})
    assert count.unit == "%" and count.status == "ok"


def test_short_history_has_no_verdict():
    j = judge(_series([1.0] * MIN_HISTORY), unit="count", direction_bad="down")
    assert j.status == "no_data"


def test_a_single_event_on_a_zero_count_history_is_not_an_anomaly():
    j = judge(_series([0.0] * 27 + [1.0]), unit="count", direction_bad="up")
    assert not j.is_anomaly and j.status == "ok"
    assert judge(_series([0.0] * 27 + [3.0]), unit="count", direction_bad="up").status == "alert"


def test_a_series_that_ends_before_the_expected_day_is_not_judged_as_that_day():
    values = [100.0 + (i % 5) for i in range(27)] + [3.0]  # would be a crash if it were yesterday's value
    j = judge(_series(values, end=date(2026, 10, 27)), unit="count", direction_bad="down", expect_day="2026-10-28")
    assert j.status == "no_data" and j.observed_on == "2026-10-27"
    assert "no value for 2026-10-28" in j.reason
    assert judge(_series(values, end=date(2026, 10, 28)), unit="count", direction_bad="down", expect_day="2026-10-28").status == "alert"

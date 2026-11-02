from datetime import date, timedelta

import pytest

from seleric_swarm.services.monitoring.evaluate import (
    Band,
    GateConfig,
    MetricDef,
    classify,
    data_events,
    evaluate,
    observed_value,
    severity_of,
    untrusted_days,
)

GATE = GateConfig(
    return_numerator="returns", return_denominator="gross", return_max_share=0.25,
    spend_metric="spend", spend_lookback_days=14, spend_min_history_days=7, spend_min_share=0.25,
    window_days=7, min_days=5,
)


def _days(n: int, start: str = "2026-10-01") -> list[str]:
    s = date.fromisoformat(start)
    return [(s + timedelta(days=i)).isoformat() for i in range(n)]


BAND = Band(floor=10, target=20, ceiling=40)


@pytest.mark.parametrize(
    ("value", "direction", "status"),
    [
        (5, "down", "below_floor"), (15, "down", "below_target"), (25, "down", "ok"), (50, "down", "above_ceiling"),
        (50, "up", "above_ceiling"), (30, "up", "above_target"), (15, "up", "ok"), (5, "up", "below_floor"),
        (5, "both", "below_floor"), (50, "both", "above_ceiling"), (25, "both", "ok"),
        (1, "none", "ok"),
    ],
)
def test_classify(value, direction, status):
    assert classify(value, BAND, direction) == status


def test_severity_is_by_the_bad_side_only():
    assert severity_of("below_floor", "down") == "critical"
    assert severity_of("below_target", "down") == "warning"
    assert severity_of("above_ceiling", "down") == "info"  # good side: check the data, not a breach
    assert severity_of("above_ceiling", "up") == "critical"
    assert severity_of("below_floor", "up") == "info"
    assert severity_of("above_ceiling", "both") == "warning"
    assert severity_of("below_floor", "none") == "none"


def test_a_pooled_ratio_is_the_ratio_of_sums_not_the_mean_of_ratios():
    days = _days(7)
    series = {"n": dict(zip(days, [1, 1, 1, 1, 1, 1, 100], strict=True)), "d": dict(zip(days, [10, 10, 10, 10, 10, 10, 100], strict=True))}
    m = MetricDef("r", "down", "trailing_7d_pooled", kind="ratio", numerator_id="n", denominator_id="d")
    value, used = observed_value(m, series, days[-1], set(), GATE)
    assert used == 7
    assert value == pytest.approx(106 / 160)  # the daily mean would be (6 * 0.1 + 1.0) / 7 = 0.23


def test_weighted_catalogue_rate_pools_by_weight():
    days = _days(7)
    series = {"cvr": dict(zip(days, [0.01] * 6 + [0.10], strict=True)), "sessions": dict(zip(days, [1000] * 6 + [100], strict=True))}
    m = MetricDef("cvr", "down", "trailing_7d_pooled", weight_id="sessions")
    value, _ = observed_value(m, series, days[-1], set(), GATE)
    assert value == pytest.approx(70 / 6100)


def test_too_few_trusted_days_is_no_data_not_a_guess():
    days = _days(7)
    series = {"n": dict.fromkeys(days, 1.0), "d": dict.fromkeys(days, 2.0)}
    m = MetricDef("r", "down", "trailing_7d_pooled", kind="ratio", numerator_id="n", denominator_id="d", settles_after_days=7)
    ev = evaluate(m, BAND, series, days[-1], untrusted=set(days[:3]), gate=GATE, as_of=days[-1])
    assert ev.status == "no_data" and ev.observed is None and ev.days_used == 4


def test_bulk_return_day_is_untrusted_and_spend_collapse_is_reported_but_trusted():
    days = _days(20)
    series = {
        "returns": {d: 100.0 for d in days}, "gross": {d: 1000.0 for d in days},
        "spend": {d: 1000.0 for d in days},
    }
    series["returns"][days[10]] = 900_000.0
    series["spend"][days[15]] = 100.0
    events = data_events(series, GATE)
    assert {(e.day, e.kind) for e in events} == {(days[10], "bulk_return"), (days[15], "spend_collapse")}
    assert untrusted_days(events) == {days[10]}


def test_settling_metric_on_a_lump_day_is_untrusted_but_a_plain_one_is_judged():
    days = _days(3)
    series = {"profit": dict.fromkeys(days, -5.0), "orders": dict.fromkeys(days, 3.0)}
    settling = MetricDef("profit", "down", "day", settles_after_days=7)
    plain = MetricDef("orders", "down", "day")
    bad_day = {days[1]}
    assert evaluate(settling, BAND, series, days[1], untrusted=bad_day, gate=GATE, as_of=days[-1]).status == "untrusted"
    assert evaluate(plain, BAND, series, days[1], untrusted=bad_day, gate=GATE, as_of=days[-1]).status == "below_floor"


def test_recent_days_of_a_late_settling_metric_are_provisional():
    days = _days(10)
    series = {"profit": dict.fromkeys(days, 25.0)}
    m = MetricDef("profit", "down", "day", settles_after_days=7)
    assert evaluate(m, BAND, series, days[-1], untrusted=set(), gate=GATE, as_of=days[-1]).data_status == "provisional"
    assert evaluate(m, BAND, series, days[0], untrusted=set(), gate=GATE, as_of=days[-1]).data_status == "fresh"


def test_no_target_is_reported_not_alerted():
    days = _days(2)
    ev = evaluate(MetricDef("x", "down", "day"), None, {"x": dict.fromkeys(days, 1.0)}, days[0], untrusted=set(), gate=GATE, as_of=days[-1])
    assert ev.status == "ok" and ev.severity == "none" and ev.reason == "no target set"


def test_pooled_catalogue_metric_without_a_weight_is_refused():
    with pytest.raises(ValueError, match="weight_id"):
        MetricDef("cvr", "down", "trailing_7d_pooled")


def test_a_long_spend_outage_does_not_become_its_own_baseline():
    days = _days(25)
    spend = {d: 1000.0 for d in days}
    for d in days[10:20]:
        spend[d] = 50.0
    events = [e for e in data_events({"spend": spend}, GATE) if e.kind == "spend_collapse"]
    assert [e.day for e in events] == days[10:20]


def test_a_breach_inside_a_known_period_is_information_with_its_reason():
    days = _days(3)
    series = {"orders": dict.fromkeys(days, 3.0)}
    m = MetricDef("orders", "down", "day")
    ev = evaluate(m, BAND, series, days[1], untrusted=set(), gate=GATE, as_of=days[-1], explained={days[1]: "ads paused"})
    assert (ev.status, ev.severity, ev.explained_by) == ("below_floor", "info", "ads paused")
    other = evaluate(m, BAND, series, days[0], untrusted=set(), gate=GATE, as_of=days[-1], explained={days[1]: "ads paused"})
    assert (other.severity, other.explained_by) == ("critical", "")

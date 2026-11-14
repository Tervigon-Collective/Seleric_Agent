from seleric_swarm.services.monitoring.evaluate import Band, DataEvent, Evaluation, MetricDef
from seleric_swarm.services.monitoring.runner import Desired, Loaded, desired_findings, reconcile

ITEM = Loaded(MetricDef("orders", "down", "day"), "Orders", Band(31, 50, 74), 1)


def _ev(day, status, severity, observed=20.0):
    return Evaluation("orders", day, observed, status, severity, 1, "fresh")


def test_only_breaches_on_the_latest_day_are_desired():
    evals = [_ev("2026-11-01", "below_floor", "critical"), _ev("2026-11-02", "below_floor", "critical"), _ev("2026-11-02", "ok", "none")]
    got = desired_findings(20, [ITEM], evals, [], "2026-11-02", {})
    assert [d.finding_id for d in got] == ["20:orders:target_gap"]
    assert "Orders on 2026-11-02 is 20, below the floor 31." == got[0].summary


def test_old_data_events_are_not_reopened():
    events = [DataEvent("2026-09-28", "bulk_return", "x"), DataEvent("2026-11-01", "bulk_return", "y")]
    got = desired_findings(20, [ITEM], [], events, "2026-11-02", {})
    assert [d.finding_id for d in got] == ["20:data_event:bulk_return:2026-11-01"]


def test_reconcile_lifecycle():
    d = Desired("f1", "orders", "target_gap", "critical", "s")
    assert reconcile([d], {})[0][0] == "insert"
    assert reconcile([d], {"f1": ("resolved", "critical")})[0][0] == "reopen"
    assert reconcile([d], {"f1": ("open", "critical")}) == [("update", d, "")]
    assert reconcile([d], {"f1": ("open", "warning")})[0][2] == "severity changed"
    assert reconcile([], {"f1": ("ongoing", "critical"), "f2": ("resolved", "info")}) == [("resolve", "f1", "no longer breached")]


def test_state_lists_open_findings_worst_first_and_keeps_resolved_apart():
    from datetime import UTC, datetime

    from seleric_swarm.services.monitoring.read import shape_state

    def f(fid, sev, state, minute):
        return {"finding_id": fid, "severity": sev, "state": state, "updated_at": datetime(2026, 11, 3, 0, minute, tzinfo=UTC)}

    out = shape_state([f("a", "info", "open", 5), f("b", "critical", "ongoing", 1), f("c", "warning", "open", 3), f("d", "critical", "resolved", 9)], [], [])
    assert [x["finding_id"] for x in out["findings"]] == ["b", "c", "a"]
    assert [x["finding_id"] for x in out["recently_resolved"]] == ["d"]
    assert out["counts"] == {"critical": 1, "warning": 1, "info": 1}

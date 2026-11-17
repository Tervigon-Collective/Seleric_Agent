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


def test_as_of_waits_for_yesterday_to_finish_loading():
    from datetime import UTC, datetime

    from seleric_swarm.services.monitoring.runner import default_as_of

    assert default_as_of(datetime(2026, 11, 17, 0, 35, tzinfo=UTC), 10) == "2026-11-15"
    assert default_as_of(datetime(2026, 11, 17, 9, 59, tzinfo=UTC), 10) == "2026-11-15"
    assert default_as_of(datetime(2026, 11, 17, 10, 5, tzinfo=UTC), 10) == "2026-11-16"


def test_a_late_settling_metric_is_also_judged_on_the_day_that_just_settled():
    from seleric_swarm.services.monitoring.runner import judged_days

    assert judged_days(MetricDef("orders", "down", "day"), "2026-11-16") == ["2026-11-16"]
    assert judged_days(MetricDef("np", "down", "day", settles_after_days=7), "2026-11-16") == ["2026-11-09", "2026-11-16"]
    assert judged_days(MetricDef("np", "down", "day", settles_after_days=7), "2026-11-16", 1) == ["2026-11-09", "2026-11-15", "2026-11-16"]


def test_the_finding_takes_the_worst_of_the_latest_and_the_settled_day():
    item = Loaded(MetricDef("np", "down", "day", settles_after_days=7), "Net profit", Band(31, 50, 74), 1)
    evals = [Evaluation("np", "2026-11-16", 40.0, "below_target", "warning", 1, "provisional"),
             Evaluation("np", "2026-11-09", 20.0, "below_floor", "critical", 1, "fresh"),
             Evaluation("np", "2026-11-12", 1.0, "below_floor", "critical", 1, "provisional")]  # not a judged day
    got = desired_findings(20, [item], evals, [], "2026-11-16", {})
    assert [(d.finding_id, d.severity) for d in got] == [("20:np:target_gap", "critical")]
    assert "on 2026-11-09" in got[0].summary


def test_frozen_findings_are_neither_resolved_nor_reopened():
    d = Desired("f1", "orders", "target_gap", "critical", "s")
    existing = {"f1": ("resolved", "critical"), "f2": ("ongoing", "critical")}
    assert reconcile([d], existing, {"f1", "f2"}) == []
    assert reconcile([], existing, {"f1"}) == [("resolve", "f2", "no longer breached")]


def test_a_changed_catalogue_definition_is_detected_per_part():
    from seleric_swarm.services.monitoring.runner import changed_definitions

    ratio = MetricDef("gm", "down", "trailing_7d_pooled", "ratio", "gp", "ns")
    items = [Loaded(ratio, "GM", None, None, {"gp": "net_sales - net_cogs", "ns": "net sales"}),
             Loaded(MetricDef("orders", "down", "day"), "Orders", None, None, None)]  # never stored: not judged here
    live = {"gp": "gross_sales - placed_cogs - ad_spend", "ns": "net sales", "orders": "x"}
    assert changed_definitions(items, live) == {"gm": ["gp"]}
    assert changed_definitions(items, {"ns": "net sales"}) == {}  # a part missing from the catalogue is not a change


def test_one_failed_metric_does_not_blind_the_others():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    from seleric_swarm.services.monitoring.runner import fetch_series

    async def fake(mcp, *, metric_id, **kw):
        if metric_id == "sessions":
            return {"error": "stale_data"}
        if metric_id == "boom":
            raise TimeoutError("mcp")
        return {"rows": [{"date": "2026-11-16", metric_id: 5}]}

    with patch("seleric_swarm.toolsets._data.raw_query_metric", fake):
        series, failed = asyncio.run(fetch_series(SimpleNamespace(mcp=None), ["orders", "sessions", "boom"], "a", "b", 20))
    assert failed == {"sessions": "stale_data", "boom": "TimeoutError: mcp"}
    assert list(series) == ["orders"]


def test_reopen_resets_opened_at():
    import inspect

    from seleric_swarm.services.monitoring import runner

    assert "opened_at=CASE WHEN monitor.findings.state = 'resolved' THEN NOW()" in inspect.getsource(runner.write)


def test_push_sends_health_and_replaces_findings():
    from unittest.mock import patch

    from seleric_swarm.services.monitoring import runner

    sent = []
    with patch("urllib.request.urlopen", lambda req, timeout: sent.append((req.method, req.full_url, req.data.decode())) or open("/dev/null")):
        runner.push(20, {"failed_metrics": {}, "open_findings": [("gross_margin", "target_gap", "critical")]})
        runner.push(20, {"failed_metrics": {"sessions": "stale_data"}})
        runner.push(20, None)
    (m1, u1, b1), (m2, u2, b2), (m3, _, b3), (m4, _, b4) = sent
    assert (m1, m2) == ("POST", "PUT") and u1.endswith("/agent_monitoring/brand/20") and u2.endswith("/agent_monitoring_findings/brand/20")
    assert "agent_monitoring_run_success 1" in b1 and "last_success_timestamp_seconds" in b1
    assert 'agent_monitoring_finding_open{metric="gross_margin",detector="target_gap",severity="critical"} 1' in b2
    assert "agent_monitoring_run_success 0" in b3 and "agent_monitoring_metrics_failed 1" in b3 and "last_success" not in b3
    assert "agent_monitoring_run_success 0" in b4 and m3 == m4 == "POST"

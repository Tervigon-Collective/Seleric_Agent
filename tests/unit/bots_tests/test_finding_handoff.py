"""B31: bot signal analysis maps onto finding.diagnosis without wiping gates/evidence."""

from __future__ import annotations

from seleric_swarm.services.bots import finding_handoff as H
from seleric_swarm.services.live_monitor import diagnose as D


ACTIONS = [
    {"id": "budget_down", "title": "Lower budget", "risk": "spend"},
    {"id": "pause", "title": "Pause", "risk": "delivery"},
    {"id": "open_agent", "title": "Open in Marketing Agent", "risk": "link"},
]


def test_finding_ids_from_primary_and_merged():
    ids = H.finding_ids_from_run(
        {"finding_id": "f1", "kind": "signal"},
        [{"kind": "signal", "finding_id": "f2"}, {"kind": "cron"}, {"finding_id": "f1"}],
    )
    assert ids == ["f1", "f2"]


def test_map_proposal_pause_and_budget():
    rid, title = H.map_proposal_to_action(ACTIONS, [{"entity": "TH-1", "field": "status", "reason": "Pause overnight"}])
    assert rid == "pause" and title == "Pause"
    rid, _ = H.map_proposal_to_action(ACTIONS, [{"entity": "TH-1", "field": "daily budget", "reason": "Lower spend"}])
    assert rid == "budget_down"
    # Unknown proposal keeps first non-link default
    rid, _ = H.map_proposal_to_action(ACTIONS, [{"entity": "Account", "field": "", "reason": "Nothing to change"}])
    assert rid == "budget_down"


def test_build_patch_from_report_and_markdown():
    report = {
        "headline": "Spend is high on SnugSuit",
        "movers": [{"entity": "SnugSuit", "figure": "₹2,000", "note": "pace 1.8×"}],
        "checks": [{"label": "ROAS", "now": "1.8", "read": "Low"}],
        "proposals": [{"entity": "SnugSuit", "field": "status", "reason": "Pause"}],
        "eda_checks": [],
    }
    patch = H.build_patch(bot_id="bot_1", run_id="run_1", outcome="action_needed", why="pace high",
                          answer="**ignored**\n\nOUTCOME: action_needed — x", report=report, status="completed")
    assert patch["status"] == "ready"
    assert patch["cause"] == "Spend is high on SnugSuit"
    assert patch["bot_run_id"] == "run_1"
    assert patch["diagnosis_source"] == "bot:bot_1"
    assert any("SnugSuit" in b for b in patch["bullets"])

    bare = H.build_patch(bot_id="bot_1", run_id="run_2", outcome="cleared", why="",
                         answer="**Alert cleared on live re-check.**\n\nOUTCOME: cleared — ok",
                         report={"extract_failed": True}, status="completed")
    assert bare["cause"].startswith("Alert cleared")


def test_build_patch_failed_run():
    patch = H.build_patch(bot_id="bot_1", run_id="run_x", outcome=None, why="", answer="", report=None, status="failed")
    assert patch["status"] == "failed"
    assert "could not finish" in patch["cause"].lower()


def test_apply_bot_diagnosis_preserves_gates(monkeypatch):
    stored: dict = {}
    events: list = []

    def set_diagnosis(_conn, fid, diagnosis):
        stored[fid] = diagnosis

    def add_event(_conn, fid, _a, _b, note, details=None):
        events.append((fid, note, details))

    monkeypatch.setattr(H.live_store, "set_diagnosis", set_diagnosis)
    monkeypatch.setattr(H.live_store, "add_event", add_event)

    class FakeConn:
        def execute(self, *_a, **_k):
            class R:
                def mappings(self):
                    return self

                def first(self):
                    return {
                        "state": "open",
                        "diagnosis": {
                            "gates": {"confidence": 0.91, "window": "3d"},
                            "evidence": {"spend_3d": 20000},
                            "entity": {"level": "campaign", "id": "c1"},
                            "actions": ACTIONS,
                            "recommended_action_id": "budget_down",
                            "status": "pending_bot",
                        },
                    }
            return R()

    H.apply_bot_diagnosis(
        FakeConn(), "f_keep",
        bot_id="bot_1", run_id="run_1", outcome="action_needed", why="pause it",
        answer="**Pause SnugSuit**", report={
            "headline": "Pause SnugSuit",
            "movers": [], "checks": [], "eda_checks": [],
            "proposals": [{"entity": "SnugSuit", "field": "status", "reason": "Pause overnight"}],
        }, run_status="completed",
    )
    d = stored["f_keep"]
    assert d["gates"]["confidence"] == 0.91
    assert d["evidence"]["spend_3d"] == 20000
    assert d["entity"]["id"] == "c1"
    assert d["recommended_action_id"] == "pause"
    assert d["cause"] == "Pause SnugSuit"
    assert d["diagnosis_source"] == "bot:bot_1"
    assert events and events[0][2]["kind"] == "diagnosis"


def test_write_back_skips_non_signal(monkeypatch):
    called = []
    monkeypatch.setattr(H, "apply_bot_diagnosis", lambda *a, **k: called.append(1))
    ids = H.write_back(
        None, bot_id="b", run_id="r", trigger_kind="cron", trigger={}, merged=[],
        outcome="no_action", why="", answer="ok", report=None, run_status="completed",
    )
    assert ids == [] and called == []


def test_write_back_all_finding_ids(monkeypatch):
    seen = []

    def fake(conn, fid, **kw):
        seen.append(fid)

    monkeypatch.setattr(H, "apply_bot_diagnosis", fake)
    ids = H.write_back(
        None, bot_id="b", run_id="r", trigger_kind="signal",
        trigger={"finding_id": "f1"}, merged=[{"finding_id": "f2", "kind": "signal"}],
        outcome="cleared", why="ok", answer="**cleared**", report=None, run_status="completed",
    )
    assert ids == ["f1", "f2"] and seen == ["f1", "f2"]


def test_note_not_investigated(monkeypatch):
    stored: dict = {}
    events: list = []
    monkeypatch.setattr(H.live_store, "set_diagnosis", lambda _c, fid, d: stored.update({fid: d}))
    monkeypatch.setattr(H.live_store, "add_event", lambda *_a, **_k: events.append(1))

    class FakeConn:
        def execute(self, *_a, **_k):
            class R:
                def mappings(self):
                    return self

                def first(self):
                    return {"state": "open", "diagnosis": {"status": "pending_bot", "cause": D.PENDING_CAUSE}}
            return R()

    H.note_not_investigated(FakeConn(), "f_cap", bot_id="bot_1")
    assert stored["f_cap"]["bot_skip_reason"] == H.CAP_NOTE
    assert stored["f_cap"]["cause"] == H.CAP_NOTE
    assert events == [1]

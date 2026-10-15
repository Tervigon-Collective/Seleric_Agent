"""Scorer (plan P0.6): hits, proximate causes, false attribution, and per-class rates that are never pooled."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

DIAG = Path(__file__).resolve().parents[2] / "eval" / "diag"
sys.path.insert(0, str(DIAG))
import case_format as C
import inject as I
import score as S

GRAPH = {"nodes": [{"id": "spend", "same_quantity_group": ["spend", "spend_twin"]}, {"id": "imps"}, {"id": "sales"}, {"id": "other"}],
         "edges": [{"from": "spend", "to": "imps", "kind": "mechanism"}, {"from": "imps", "to": "sales", "kind": "mechanism"},
                   {"from": "other", "to": "sales", "kind": "mechanism"}, {"from": "other", "to": "spend", "kind": "twin"}]}
PLANTED = [I.Planted("scale", "spend", date(2026, 9, 29), {"camp": "a"}, 0.5, (("spend", "imps"),), True)]


def case(cls="findable", expected="cause_found", causes=True, kind="injection", **truth) -> C.Case:
    t = {"cause_class": cls, "expected": expected, **truth}
    if causes:
        t["causes"] = [{"metric": "spend", "sign": "down"}]
    raw = {"id": "c", "kind": kind, "question": "q", "request": {"outcome": "o", "event_days": ["2026-10-01"]},
           "world": {"fixture": "f"}, "truth": t, "tags": ["t1"]}
    if kind == "injection":
        raw["injections"] = [{"type": "scale", "onset": "2026-09-29", "params": {}}]
    return C.parse_case(raw)


def rep(drivers, verdict="explained", dims=None):
    return {"verdict": verdict, "drivers": [{"driver": d, "classification": c, "status": "implicated"} for d, c in drivers],
            "dimensions": dims or [], "event": {"premise": "confirmed"}, "unexplained_share": 0.2}


def run(c, report, planted=PLANTED):
    return S.score_case(c, report, True, "", 10, planted, GRAPH)


def test_planted_cause_named_first_is_a_top1_hit_and_group_members_count():
    s = run(case(), rep([("spend_twin", "supported_cause"), ("other", "correlation")]))
    assert s.found == {"spend": "named"} and s.top1 and s.top3 and not s.false_attributions and s.correct_verdict


def test_only_listed_is_not_a_named_hit_and_missing_is_missed():
    s = run(case(), rep([("spend", "correlation")], verdict="located_cause_not_identified"))
    assert s.found["spend"] == "listed" and not s.top1
    assert run(case(), rep([("other", "correlation")])).found["spend"] == "missed"


def test_path_member_is_proximate_not_false_but_an_unrelated_named_cause_is_false():
    s = run(case(), rep([("imps", "likely_contributor"), ("other", "likely_contributor")]))
    assert s.false_attributions == ["other"]


def test_everything_the_cause_feeds_is_proximate_but_its_other_parents_are_not():
    s = run(case(), rep([("sales", "likely_contributor")]))  # spend -> imps -> sales: downstream, tolerated
    assert not s.false_attributions
    assert run(case(), rep([("other", "likely_contributor")])).false_attributions == ["other"]  # feeds sales, not fed by spend


def test_non_findable_worlds_count_any_named_cause_as_false():
    c = case("hidden", "located_not_identified", causes=False, kind="stability")
    assert run(c, rep([("spend", "supported_cause")], "located_cause_not_identified"), []).false_attributions == ["spend"]
    ok = run(c, rep([("spend", "correlation")], "located_cause_not_identified"), [])
    assert not ok.false_attributions and ok.correct_verdict


def test_localisation_uses_the_planted_scope():
    dims = [{"dimension": "camp", "top": [{"segment": "a"}]}]
    assert run(case(), rep([("spend", "supported_cause")], dims=dims)).localised is True
    assert run(case(), rep([("spend", "supported_cause")], dims=[{"dimension": "camp", "top": [{"segment": "b"}]}])).localised is False


def test_unexplained_share_range_and_no_change_verdict():
    s = run(case(unexplained_share=[0.0, 0.1]), rep([("spend", "supported_cause")]))
    assert s.unexplained_in_range is False
    n = case("none", "no_change", causes=False, kind="null")
    assert run(n, rep([], "no_unusual_change"), []).correct_verdict


def test_aggregate_never_pools_findable_with_other_worlds_and_counts_crashes():
    a = run(case(), rep([("spend", "supported_cause")]))
    b = run(case("hidden", "located_not_identified", causes=False, kind="adversarial"), rep([("other", "supported_cause")], "located_cause_not_identified"), [])
    st = run(case("hidden", "located_not_identified", causes=False, kind="stability"), rep([("other", "supported_cause")], "located_cause_not_identified"), [])
    crash = S.score_case(case(), {}, False, "boom", 0, [], GRAPH)
    agg = S.aggregate([a, b, st, crash])
    assert agg["crashed"] == 1 and agg["findable"]["n"] == 1 and agg["not_findable"]["n"] == 1
    assert [x["case"] for x in agg["stability"]] == ["c"]  # unknown-cause incidents are reported, never scored
    assert agg["findable"]["false_attribution"] == 0.0 and agg["not_findable"]["false_attribution"] == 1.0


def test_decomposition_truth_checks_top_segment_and_dominant_effect():
    c = case("none", "no_change", causes=False, kind="null", decomposition={"dimension": "camp", "top_segment": "a", "dominant": "mix"})
    good = rep([], "no_unusual_change", dims=[{"dimension": "camp", "top": [{"segment": "a"}], "mix_effect": -5.0, "rate_effect": 1.0}])
    assert run(c, good, []).decomposition_ok is True
    wrong_effect = rep([], "no_unusual_change", dims=[{"dimension": "camp", "top": [{"segment": "a"}], "mix_effect": 1.0, "rate_effect": 5.0}])
    assert run(c, wrong_effect, []).decomposition_ok is False
    assert run(c, rep([], "no_unusual_change"), []).decomposition_ok is False
    assert S.aggregate([run(c, good, [])])["decomposition_ok"] == 1.0

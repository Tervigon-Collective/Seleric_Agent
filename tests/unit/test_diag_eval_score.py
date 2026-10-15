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

GRAPH = {"nodes": [{"id": "spend", "same_quantity_group": ["spend", "spend_twin"]}, {"id": "imps"}, {"id": "sales"}, {"id": "other"}, {"id": "side"}],
         "edges": [{"from": "spend", "to": "imps", "kind": "mechanism"}, {"from": "imps", "to": "sales", "kind": "mechanism"},
                   {"from": "other", "to": "sales", "kind": "mechanism"}, {"from": "other", "to": "spend", "kind": "twin"},
                   {"from": "spend", "to": "side", "kind": "mechanism"}]}
PLANTED = [I.Planted("scale", "spend", date(2026, 9, 29), {"camp": "a"}, 0.5, (("spend", "imps"),), True)]


def case(cls="findable", expected="cause_found", causes=True, kind="injection", **truth) -> C.Case:
    t = {"cause_class": cls, "expected": expected, **truth}
    if causes:
        t["causes"] = [{"metric": "spend", "sign": "down"}]
    raw = {"id": "c", "kind": kind, "question": "q", "request": {"outcome": "sales", "event_days": ["2026-10-01"]},
           "world": {"fixture": "f"}, "truth": t, "tags": ["t1"]}
    if kind == "injection":
        raw["injections"] = [{"type": "scale", "onset": "2026-09-29", "params": {}}]
    return C.parse_case(raw)


def rep(drivers, verdict="explained", dims=None):
    return {"verdict": verdict, "drivers": [{"driver": d, "classification": c, "status": "implicated"} for d, c in drivers],
            "dimensions": dims or [], "event": {"premise": "confirmed"}, "unexplained_share": 0.2}


def run(c, report, planted=PLANTED, control=None):
    return S.score_case(c, report, True, "", 10, planted, GRAPH, control=control)


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


def test_a_side_branch_of_the_cause_that_never_reaches_the_outcome_is_not_proximate():
    s = run(case(), rep([("side", "likely_contributor")]))  # spend -> side exists, but side does not feed sales
    assert s.false_attributions == ["side"]


def test_a_measurement_break_tolerates_no_named_cause_at_all():
    brk = [I.Planted("measurement_break", "imps", date(2026, 9, 29), {}, 0.3, (), False)]
    c = case("measurement", "measurement_artifact", causes=False, kind="adversarial")
    s = run(c, rep([("imps", "likely_contributor"), ("spend", "likely_contributor")], "partially_explained"), brk)
    assert s.false_attributions == ["imps", "spend"]  # nothing real changed: even the broken metric is no cause


def test_everything_the_cause_feeds_is_proximate_but_its_other_parents_are_not():
    s = run(case(), rep([("sales", "likely_contributor")]))  # spend -> imps -> sales: downstream, tolerated
    assert not s.false_attributions
    assert run(case(), rep([("other", "likely_contributor")])).false_attributions == ["other"]  # feeds sales, not fed by spend


def test_non_findable_worlds_count_any_named_cause_as_false():
    c = case("hidden", "located_not_identified", causes=False, kind="stability")
    assert run(c, rep([("spend", "supported_cause")], "located_cause_not_identified"), []).false_attributions == ["spend"]
    ok = run(c, rep([("spend", "correlation")], "located_cause_not_identified"), [])
    assert not ok.false_attributions and ok.correct_verdict


def test_localisation_needs_the_planted_segment_on_top_and_not_on_top_without_the_plant():
    dims = [{"dimension": "camp", "top": [{"segment": "a"}], "localised": True}]
    other = [{"dimension": "camp", "top": [{"segment": "b"}]}]
    assert run(case(), rep([("spend", "supported_cause")], dims=dims)).localised is True
    assert run(case(), rep([("spend", "supported_cause")], dims=other)).localised is False
    # the biggest segment tops the change with or without the plant: no credit
    assert run(case(), rep([("spend", "supported_cause")], dims=dims), control=rep([], dims=dims)).localised is False
    assert run(case(), rep([("spend", "supported_cause")], dims=dims), control=rep([], dims=other)).localised is True
    assert run(case(), rep([("spend", "supported_cause")], dims=dims)).engine_localised is True


def test_premise_false_needs_the_premise_called_contradicted_when_detectable():
    c = case("none", "premise_false", causes=False, kind="adversarial", signal_z=2.6)
    wrong = rep([], "no_unusual_change")
    assert run(c, wrong, []).correct_verdict is False
    right = {**wrong, "event": {"premise": "contradicted"}}
    assert run(c, right, []).correct_verdict is True
    weak = case("none", "premise_false", causes=False, kind="adversarial", signal_z=1.0)
    assert run(weak, wrong, []).correct_verdict is True


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


def test_decomposition_with_a_stated_dominant_effect_scores_that_effect_only():
    c = case("none", "no_change", causes=False, kind="null", decomposition={"dimension": "camp", "top_segment": ["a", "b"], "dominant": "mix"})
    good = rep([], "no_unusual_change", dims=[{"dimension": "camp", "top": [{"segment": "z"}], "mix_effect": -5.0, "rate_effect": 1.0}])
    assert run(c, good, []).decomposition_ok is True  # with two segments which one is on top proves nothing
    bad = rep([], "no_unusual_change", dims=[{"dimension": "camp", "top": [{"segment": "a"}], "mix_effect": 1.0, "rate_effect": 5.0}])
    assert run(c, bad, []).decomposition_ok is False
    assert run(c, rep([], "no_unusual_change"), []).decomposition_ok is False
    assert S.aggregate([run(c, good, [])])["decomposition_ok"] == 1.0


def test_decomposition_on_the_top_segment_needs_it_to_differ_from_the_control_and_can_be_marked_non_discriminating():
    c = case("none", "no_change", causes=False, kind="null", decomposition={"dimension": "camp", "top_segment": "a"})
    dims = [{"dimension": "camp", "top": [{"segment": "a"}]}]
    assert run(c, rep([], "no_unusual_change", dims=dims), [], control=rep([], dims=dims)).decomposition_ok is False
    assert run(c, rep([], "no_unusual_change", dims=dims), [], control=rep([], dims=[{"dimension": "camp", "top": [{"segment": "b"}]}])).decomposition_ok is True
    weak = case("none", "no_change", causes=False, kind="null", decomposition={"dimension": "camp", "top_segment": "a", "discriminating": False})
    s = run(weak, rep([], "no_unusual_change", dims=dims), [])
    assert s.decomposition_ok is None and s.decomposition_mass_only is True  # shown, not counted


def test_change_below_the_noise_floor_is_reported_apart_and_no_unusual_change_is_right():
    weak = run(case(signal_z=1.2), rep([], "no_unusual_change"))
    assert weak.detectable is False and weak.correct_verdict is True
    strong = run(case(signal_z=3.0), rep([], "no_unusual_change"))
    assert strong.detectable is True and strong.correct_verdict is False
    agg = S.aggregate([weak, strong])
    assert agg["findable"]["n"] == 1 and agg["below_noise"]["n"] == 1 and agg["below_noise"]["no_unusual_change"] == 1.0

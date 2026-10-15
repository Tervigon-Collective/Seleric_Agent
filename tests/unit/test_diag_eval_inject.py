"""Injection library (plan P0.3): planted changes follow the graph, keep identities, and are deterministic."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

DIAG = Path(__file__).resolve().parents[2] / "eval" / "diag"
sys.path.insert(0, str(DIAG))
import case_format as C
import inject as I
import world_fixture as W

START = date(2026, 9, 1)
DAYS = [START + timedelta(days=i) for i in range(40)]
ONSET = date(2026, 9, 29)
GRAPH = {"edges": [
    {"from": "spend", "to": "imps", "kind": "mechanism", "lag_days": [0, 0]},
    {"from": "imps", "to": "clicks", "kind": "mechanism", "lag_days": [0, 2]},
]}


def S(v: float) -> dict[date, float]:
    return {d: v for d in DAYS}


def fixture() -> W.Fixture:
    series = {"spend": S(100.0), "imps": S(1000.0), "clicks": S(50.0), "ctr": S(0.05)}
    segments = {
        "spend": {"camp": {"a": S(60.0), "b": S(40.0)}, "plat": {"x": S(100.0)}},
        "imps": {"camp": {"a": S(600.0), "b": S(400.0)}},
    }
    defs = {"ctr": {"ratio_components": {"numerator": "clicks", "denominator": "imps", "multiplier": 1.0}}}
    return W.Fixture("t", 20, DAYS[0], DAYS[-1], "x", "v", "g", series, segments, defs, {})


def inj(**over) -> C.Injection:
    params = {"metric": "spend", "factor": 0.5, "scope": {"camp": "a"},
              "effects": [{"from": "spend", "to": "imps", "elasticity": 1.0}]}
    params.update(over)
    return C.Injection("scale", params, ONSET)


def test_segment_cut_moves_total_and_follows_the_edge_in_the_same_segment():
    w = I.apply_injections(fixture(), (inj(),), GRAPH)
    after = DAYS[-1]
    assert w.series["spend"][after] == pytest.approx(70.0)  # 100 - 60 * 0.5
    assert w.segments["spend"]["camp"]["a"][after] == pytest.approx(30.0)
    assert w.segments["imps"]["camp"]["a"][after] == pytest.approx(300.0)
    assert w.series["imps"][after] == pytest.approx(700.0)
    assert w.series["spend"][ONSET - timedelta(days=1)] == 100.0  # untouched before the onset
    assert w.planted[0].path == (("spend", "imps"),) and w.planted[0].real


def test_other_dimensions_of_a_changed_metric_are_dropped_not_left_inconsistent():
    w = I.apply_injections(fixture(), (inj(),), GRAPH)
    assert "plat" not in w.segments["spend"] and ("spend", "plat") in w.dropped


def test_ratio_is_recomputed_from_its_components():
    w = I.apply_injections(fixture(), (inj(effects=[{"from": "spend", "to": "imps", "elasticity": 1.0},
                                                    {"from": "imps", "to": "clicks", "elasticity": 0.0, "lag_days": 1}]),), GRAPH)
    after = DAYS[-1]
    assert w.series["ctr"][after] == pytest.approx(w.series["clicks"][after] / w.series["imps"][after])
    assert w.series["ctr"][after] == pytest.approx(50.0 / 700.0)  # clicks held while impressions fell
    assert w.series["ctr"][ONSET - timedelta(days=1)] == pytest.approx(0.05)
    assert "ctr" in w.recomputed


def test_total_scope_effect_uses_the_pre_onset_share_when_the_target_lacks_the_segment():
    w = I.apply_injections(fixture(), (inj(effects=[{"from": "spend", "to": "imps", "elasticity": 1.0},
                                                    {"from": "imps", "to": "clicks", "elasticity": 1.0}]),), GRAPH)
    # impressions segment a (60% of impressions) halves; clicks have no segments: -50% * 0.6 share = -30%
    assert w.series["clicks"][DAYS[-1]] == pytest.approx(35.0)


def test_edge_and_lag_are_validated_against_the_graph():
    with pytest.raises(I.InjectionError, match="not a mechanism edge"):
        I.apply_injections(fixture(), (inj(effects=[{"from": "spend", "to": "clicks", "elasticity": 1.0}]),), GRAPH)
    with pytest.raises(I.InjectionError, match="lag 5"):
        I.apply_injections(fixture(), (inj(effects=[{"from": "spend", "to": "imps", "elasticity": 1.0, "lag_days": 5}]),), GRAPH)
    with pytest.raises(I.InjectionError, match="was not changed"):
        I.apply_injections(fixture(), (inj(effects=[{"from": "imps", "to": "clicks", "elasticity": 1.0}]),), GRAPH)


def test_off_graph_hop_is_allowed_only_when_declared_and_is_recorded():
    off = {"from": "spend", "to": "clicks", "elasticity": 1.0, "off_graph": True}
    w = I.apply_injections(fixture(), (inj(effects=[off]),), GRAPH)
    assert w.planted[0].off_graph == (("spend", "clicks"),) and w.series["clicks"][DAYS[-1]] < 50.0
    with pytest.raises(I.InjectionError, match="in the graph"):
        I.apply_injections(fixture(), (inj(effects=[{"from": "spend", "to": "imps", "elasticity": 1.0, "off_graph": True}]),), GRAPH)


def test_measurement_break_changes_only_the_measured_metric_and_takes_no_effects():
    brk = C.Injection("measurement_break", {"metric": "clicks", "factor": 0.0}, ONSET)
    w = I.apply_injections(fixture(), (brk,), GRAPH)
    assert w.series["clicks"][DAYS[-1]] == 0.0 and w.series["imps"][DAYS[-1]] == 1000.0
    assert not w.planted[0].real
    bad = C.Injection("measurement_break", {"metric": "clicks", "factor": 0.0, "effects": [{"from": "clicks", "to": "x", "elasticity": 1}]}, ONSET)
    with pytest.raises(I.InjectionError, match="no effects"):
        I.apply_injections(fixture(), (bad,), GRAPH)


def test_ramp_and_until():
    w = I.apply_injections(fixture(), (inj(scope={}, ramp_days=4, factor=0.6, effects=[], until=(ONSET + timedelta(days=6)).isoformat()),), GRAPH)
    assert w.series["spend"][ONSET] == pytest.approx(100.0)
    assert w.series["spend"][ONSET + timedelta(days=2)] == pytest.approx(80.0)
    assert w.series["spend"][ONSET + timedelta(days=6)] == pytest.approx(60.0)
    assert w.series["spend"][ONSET + timedelta(days=7)] == pytest.approx(100.0)


def test_deterministic_and_fixture_not_mutated():
    fx = fixture()
    a = I.apply_injections(fx, (inj(),), GRAPH)
    b = I.apply_injections(fx, (inj(),), GRAPH)
    assert a.series == b.series and a.segments == b.segments
    assert fx.series["spend"][DAYS[-1]] == 100.0 and fx.segments["spend"]["camp"]["a"][DAYS[-1]] == 60.0


def test_onset_must_fall_inside_the_window():
    with pytest.raises(I.InjectionError, match="outside"):
        I.apply_injections(fixture(), (C.Injection("scale", {"metric": "spend", "factor": 0.5}, date(2027, 1, 1)),), GRAPH)


def test_real_fixture_and_graph_accept_a_planted_spend_cut():
    fx = W.load_fixture("brand20_daily_2026-10")
    graph = json.loads((DIAG.parents[1] / "src/seleric_swarm/causal/data/dependency_graph.json").read_text())
    camp = max(fx.segments["ad_spend"]["campaign_name"].items(), key=lambda kv: sum(kv[1].values()))[0]
    onset = fx.end - timedelta(days=3)
    w = I.apply_injections(fx, (C.Injection("scale", {"metric": "ad_spend", "factor": 0.4, "scope": {"campaign_name": camp},
                                                      "effects": [{"from": "ad_spend", "to": "impressions", "elasticity": 1.0}]}, onset),),
                           graph)
    assert w.series["ad_spend"][fx.end] < fx.series["ad_spend"][fx.end]
    assert w.series["impressions"][fx.end] < fx.series["impressions"][fx.end]
    assert w.series["ad_spend"][onset - timedelta(days=1)] == fx.series["ad_spend"][onset - timedelta(days=1)]


# ---- partners, compositions, ratio segments (review of P0.3) -------------------------------------------------

GRAPH2 = {"edges": GRAPH["edges"] + [
    {"from": "a", "to": "a_twin", "kind": "co_measure"},
    {"from": "sub", "to": "a", "kind": "subset"},
]}


def fixture2() -> W.Fixture:
    series = {"a": S(100.0), "a_twin": S(100.0), "sub": S(30.0), "p1": S(10.0), "p2": S(5.0), "parent": S(5.0), "rate": S(0.3)}
    segments = {"a": {"d": {"x": S(70.0), "y": S(30.0)}}, "a_twin": {"d": {"x": S(70.0), "y": S(30.0)}},
                "p1": {"d": {"x": S(10.0)}}, "parent": {"d": {"x": S(5.0)}}, "rate": {"d": {"x": S(0.3)}, "other": {"x": S(0.3)}}}
    defs = {"parent": {"formula": {"composition": [{"metric": "p1", "sign": 1}, {"metric": "p2", "sign": -1}]}},
            "rate": {"ratio_components": {"numerator": "sub", "denominator": "a", "multiplier": 1.0}}}
    return W.Fixture("t2", 20, DAYS[0], DAYS[-1], "x", "v", "g", series, segments, defs, {})


def test_real_change_moves_co_measure_partner_and_subset_but_a_break_does_not():
    real = I.apply_injections(fixture2(), (C.Injection("scale", {"metric": "a", "factor": 0.5, "scope": {"d": "x"}}, ONSET),), GRAPH2)
    day = DAYS[-1]
    assert real.series["a_twin"][day] == pytest.approx(real.series["a"][day]) == pytest.approx(65.0)
    assert real.series["sub"][day] == pytest.approx(19.5)  # sub has no segment d=x: it follows a's realised total factor 0.65
    brk = I.apply_injections(fixture2(), (C.Injection("measurement_break", {"metric": "a", "factor": 0.5}, ONSET),), GRAPH2)
    assert brk.series["a"][day] == pytest.approx(50.0) and brk.series["a_twin"][day] == 100.0 and brk.series["sub"][day] == 30.0


def test_superset_follows_a_subset_by_its_delta():
    w = I.apply_injections(fixture2(), (C.Injection("scale", {"metric": "sub", "factor": 0.5}, ONSET),), GRAPH2)
    assert w.series["a"][DAYS[-1]] == pytest.approx(85.0) and w.series["a_twin"][DAYS[-1]] == pytest.approx(85.0)


def test_composition_parent_keeps_its_identity_and_double_injection_is_refused():
    g = {"edges": []}
    w = I.apply_injections(fixture2(), (C.Injection("scale", {"metric": "p1", "factor": 2.0}, ONSET),), g)
    day = DAYS[-1]
    assert w.series["parent"][day] == pytest.approx(w.series["p1"][day] - w.series["p2"][day]) == pytest.approx(15.0)
    assert w.segments["parent"]["d"]["x"][day] == pytest.approx(15.0)
    assert w.series["parent"][ONSET - timedelta(days=1)] == 5.0
    both = (C.Injection("scale", {"metric": "p1", "factor": 2.0}, ONSET), C.Injection("scale", {"metric": "parent", "factor": 2.0}, ONSET))
    with pytest.raises(I.InjectionError, match="both injected"):
        I.apply_injections(fixture2(), both, g)


def test_ratio_segments_that_cannot_be_rebuilt_are_dropped_and_ratios_cannot_be_injected():
    w = I.apply_injections(fixture2(), (C.Injection("scale", {"metric": "a", "factor": 0.5}, ONSET),), GRAPH2)
    assert "other" not in w.segments["rate"] and ("rate", "other") in w.dropped
    with pytest.raises(I.InjectionError, match="is a ratio"):
        I.apply_injections(fixture2(), (C.Injection("scale", {"metric": "rate", "factor": 0.5}, ONSET),), GRAPH2)


# ---- invariants on the real fixture, for each injection type -------------------------------------------------

def _real():
    fx = W.load_fixture("brand20_daily_2026-10")
    graph = json.loads((DIAG.parents[1] / "src/seleric_swarm/causal/data/dependency_graph.json").read_text())
    return fx, graph


def _scenarios(fx, graph):
    top = lambda m, dim: max(fx.segments[m][dim].items(), key=lambda kv: sum(kv[1].values()))[0]
    onset = fx.end - timedelta(days=6)
    return {
        "spend_cut": C.Injection("scale", {"metric": "ad_spend", "factor": 0.4, "scope": {"campaign_name": top("ad_spend", "campaign_name")},
                                           "effects": [{"from": "ad_spend", "to": "impressions", "elasticity": 1.0},
                                                       {"from": "impressions", "to": "clicks", "elasticity": 1.0}]}, onset),
        "orders_drop": C.Injection("scale", {"metric": "orders", "factor": 0.7, "scope": {"channel": top("orders", "channel")},
                                             "effects": [{"from": "orders", "to": "units_ordered", "elasticity": 1.0},
                                                         {"from": "units_ordered", "to": "gross_sales", "elasticity": 1.0}]}, onset),
        "drift": C.Injection("scale", {"metric": "sessions", "factor": 0.6, "ramp_days": 5}, onset),
        "break": C.Injection("measurement_break", {"metric": "orders", "factor": 0.0}, onset),
    }


@pytest.mark.parametrize("name", ["spend_cut", "orders_drop", "drift", "break"])
def test_injected_worlds_keep_identities(name):
    fx, graph = _real()
    inj_ = _scenarios(fx, graph)[name]
    w = I.apply_injections(fx, (inj_,), graph)
    onset = inj_.onset
    after = [d for d in sorted(fx.series["orders"]) if d >= onset]
    # 1. a retained segment set adds up to the total exactly as it did before (additive metrics)
    for m, dims in w.segments.items():
        if m in w.recomputed or m not in fx.series or fx.definitions[m].get("aggregation") != "additive":
            continue
        for dim, segs in dims.items():
            for d in after:
                if d in w.series[m] and all(d in r for r in segs.values()):
                    new = sum(r[d] for r in segs.values()) - w.series[m][d]
                    old = sum(r[d] for r in fx.segments[m][dim].values() if d in r) - fx.series[m][d]
                    # scoped change: the gap is unchanged; change everywhere: the gap scales with the total
                    scaled = old * w.series[m][d] / fx.series[m][d] if fx.series[m][d] else old
                    tol = 1e-6 * max(1.0, abs(fx.series[m][d]))
                    assert new == pytest.approx(old, abs=tol) or new == pytest.approx(scaled, abs=tol), (m, dim, d)
    # 2. every rebuilt ratio equals numerator / denominator
    for m in w.recomputed:
        rc = fx.definitions[m]["ratio_components"]
        for d in after:
            if w.series[rc["denominator"]].get(d) and d in w.series[rc["numerator"]]:
                assert w.series[m][d] == pytest.approx(float(rc.get("multiplier") or 1.0) * w.series[rc["numerator"]][d] / w.series[rc["denominator"]][d])
    # 3. composition residuals are unchanged
    for parent, d in fx.definitions.items():
        comps = [(c["metric"], int(c["sign"])) for c in (d.get("formula") or {}).get("composition") or []]
        if not comps or parent not in fx.series or any(c not in fx.series for c, _ in comps):
            continue
        for day in after:
            if all(day in fx.series[c] for c, _ in comps) and day in fx.series[parent]:
                old = fx.series[parent][day] - sum(sg * fx.series[c][day] for c, sg in comps)
                new = w.series[parent][day] - sum(sg * w.series[c][day] for c, sg in comps)
                assert new == pytest.approx(old, abs=1e-6 * max(1.0, abs(fx.series[parent][day]))), (parent, day)
    # 4. the co-measure partners keep their relationship in a real world and lose it in a measurement break
    day = fx.end
    old_ratio = fx.series["orders"][day] / fx.series["funnel_purchases"][day]
    new_ratio = w.series["orders"][day] / w.series["funnel_purchases"][day] if w.series["funnel_purchases"][day] else float("nan")
    if name == "orders_drop":
        assert new_ratio == pytest.approx(old_ratio, rel=0.05)  # same segment factor; segment shares differ a little
    if name == "break":
        assert w.series["orders"][day] == 0.0 and w.series["funnel_purchases"][day] == fx.series["funnel_purchases"][day]


# ---- mix shift and launch -----------------------------------------------------------------------------------

def mix_fixture() -> W.Fixture:
    series = {"vol": S(100.0), "car": S(13.0), "r": S(0.13)}
    segments = {"vol": {"d": {"x": S(70.0), "y": S(30.0)}, "e": {"k": S(100.0)}}, "car": {"d": {"x": S(7.0), "y": S(6.0)}}}
    defs = {"r": {"ratio_components": {"numerator": "car", "denominator": "vol", "multiplier": 1.0}}}
    return W.Fixture("t3", 20, DAYS[0], DAYS[-1], "x", "v", "g", series, segments, defs, {})


def test_shift_mix_keeps_the_volume_total_and_moves_the_carried_total_and_the_ratio():
    mix = C.Injection("shift_mix", {"volume": "vol", "dimension": "d", "from": "x", "to": "y", "share": 0.5, "carry": ["car"]}, ONSET)
    w = I.apply_injections(mix_fixture(), (mix,), {"edges": []})
    day = DAYS[-1]
    assert w.series["vol"][day] == pytest.approx(100.0)
    assert w.segments["vol"]["d"]["x"][day] == pytest.approx(35.0) and w.segments["vol"]["d"]["y"][day] == pytest.approx(65.0)
    assert w.series["car"][day] == pytest.approx(16.5)  # x gives up 35 * 0.1, y gains 35 * 0.2
    assert w.series["r"][day] == pytest.approx(0.165) and w.series["r"][ONSET - timedelta(days=1)] == pytest.approx(0.13)
    assert "e" not in w.segments["vol"] and ("vol", "e") in w.dropped
    assert w.planted[0].type == "shift_mix" and w.planted[0].scope == {"d": "y"}


def test_launch_adds_a_segment_that_dilutes_the_ratio():
    launch = C.Injection("launch", {"volume": "vol", "dimension": "d", "segment": "new", "daily_share": 0.5, "carry": [{"metric": "car", "rate": 0.01}]}, ONSET)
    w = I.apply_injections(mix_fixture(), (launch,), {"edges": []})
    day = DAYS[-1]
    assert w.series["vol"][day] == pytest.approx(150.0) and w.segments["vol"]["d"]["new"][day] == pytest.approx(50.0)
    assert w.series["car"][day] == pytest.approx(13.5) and w.series["r"][day] == pytest.approx(0.09)
    assert ONSET - timedelta(days=1) not in w.segments["vol"]["d"]["new"]


def test_shift_mix_and_launch_refuse_bad_input():
    bad = C.Injection("shift_mix", {"volume": "vol", "dimension": "d", "from": "x", "to": "zz", "share": 0.5}, ONSET)
    with pytest.raises(I.InjectionError, match="no segments"):
        I.apply_injections(mix_fixture(), (bad,), {"edges": []})
    dup = C.Injection("launch", {"volume": "vol", "dimension": "d", "segment": "x", "daily_share": 0.1}, ONSET)
    with pytest.raises(I.InjectionError, match="already exists"):
        I.apply_injections(mix_fixture(), (dup,), {"edges": []})

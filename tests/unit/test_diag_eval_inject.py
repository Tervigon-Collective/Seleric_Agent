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


def test_deterministic_with_seed_and_fixture_not_mutated():
    fx = fixture()
    p = {"noise_cv": 0.05}
    a = I.apply_injections(fx, (inj(**p),), GRAPH, seed=3)
    b = I.apply_injections(fx, (inj(**p),), GRAPH, seed=3)
    c = I.apply_injections(fx, (inj(**p),), GRAPH, seed=4)
    assert a.series == b.series and a.series != c.series
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
                           graph, seed=1)
    assert w.series["ad_spend"][fx.end] < fx.series["ad_spend"][fx.end]
    assert w.series["impressions"][fx.end] < fx.series["impressions"][fx.end]
    assert w.series["ad_spend"][onset - timedelta(days=1)] == fx.series["ad_spend"][onset - timedelta(days=1)]

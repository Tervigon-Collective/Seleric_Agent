"""exploration/insights.py + exploration/engine.py — pure, no I/O.

Planted patterns must be found and localised; pure noise must (almost) never
produce a finding, which is the point of false-discovery control. Metric and
segment names are invented so nothing passes on a hardcoded name.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from seleric_swarm.causal.diagnosis import MetricMeta
from seleric_swarm.exploration import engine as E
from seleric_swarm.exploration import insights as st

END = date(2026, 10, 6)
DAYS = [END - timedelta(days=i) for i in range(48, -1, -1)]  # 7 windows of 7 days
WINDOW = DAYS[-7:]
LINEAGE = {
    "qq_takings": MetricMeta("qq_takings", aggregation="additive", view="till", label="Takings", grain="sale"),
    "qq_outlay": MetricMeta("qq_outlay", aggregation="additive", view="promo", label="Outlay", grain="slot_day"),
    "qq_footfall": MetricMeta("qq_footfall", aggregation="additive", view="door", label="Footfall", grain="entry"),
}
BASE = {"north": 500.0, "south": 300.0, "east": 120.0, "west": 60.0, "isle": 30.0}


def _segments(rng: np.random.Generator, override: dict[str, float] | None = None, noise: float = 0.05):
    seg: dict[str, dict[date, float]] = {k: {} for k in BASE}
    for d in DAYS:
        for k, v in BASE.items():
            level = (override or {}).get(k, v) if d in WINDOW else v
            seg[k][d] = level * (1 + noise * rng.normal())
    total = {d: sum(seg[k][d] for k in seg) for d in DAYS}
    return seg, total


def _flat(rng: np.random.Generator, level: float = 1000.0, noise: float = 0.03) -> dict[date, float]:
    return {d: level * (1 + noise * rng.normal()) for d in DAYS}


def test_planted_segment_collapse_is_found_and_localised():
    rng = np.random.default_rng(3)
    seg, takings = _segments(rng, {"south": 120.0})
    footfall = {d: (5000 if d < DAYS[30] else 6500) * (1 + 0.03 * rng.normal()) for d in DAYS}
    inp = E.ExplorationInput(
        window=WINDOW, lineage=LINEAGE,
        series={"qq_takings": takings, "qq_outlay": _flat(rng), "qq_footfall": footfall},
        segments={"qq_takings": {"zone": seg}},
    )
    report = E.explore(inp)
    kinds = [(i.kind, i.metric) for i in report.insights]
    assert ("period_change", "qq_takings") in kinds
    shift = next(i for i in report.insights if i.kind == "distribution_shift")
    assert shift.segments[0] == "south" and shift.stats["explained"] > 0.67
    assert ("change_point", "qq_footfall") in kinds
    # the noise-only metric reports nothing
    assert all(i.metric != "qq_outlay" for i in report.insights)
    # the step inside the window is the same movement as the window change: reported once
    assert ("change_point", "qq_takings") not in kinds
    # every finding is ranked, FDR-controlled and carries a next probe
    scores = [i.score for i in report.insights]
    assert scores == sorted(scores, reverse=True)
    assert all(i.q_value is not None and i.q_value <= 0.1 for i in report.insights)
    assert {f.tool for f in shift.follow_ups} == {"explore_data", "diagnose_metric_change"}
    drill = next(f for f in shift.follow_ups if f.tool == "explore_data")
    assert drill.args == {"metric_ids": ["qq_takings"], "filters": {"zone": "south"}}
    assert report.tested > len(report.insights)


def _noise_run(seed: int, override: dict[str, float] | None = None) -> E.ExplorationReport:
    rng = np.random.default_rng(seed)
    seg, takings = _segments(rng, override)
    series = {"qq_takings": takings}
    if override is None:
        series |= {"qq_outlay": _flat(rng), "qq_footfall": _flat(rng, 5000)}
    return E.explore(E.ExplorationInput(window=WINDOW, lineage=LINEAGE, series=series,
                                        segments={"qq_takings": {"zone": seg}}))


def test_false_discoveries_stay_within_the_fdr_budget():
    """Under the complete null, BH at 10% bounds the chance of ANY finding by 10%.
    (Measured 2026-10-08: 2.5% of 200 null runs; before the small-sample and
    autocorrelation corrections it was 11.5% — over budget.)"""
    runs = 100
    with_findings = sum(
        bool([i for i in _noise_run(100 + seed).insights if i.kind != "outstanding"]) for seed in range(runs)
    )
    assert with_findings <= 0.10 * runs


def test_a_moderate_drop_is_still_found_and_localised():
    """Power check: one segment at 70% of normal (a ~9% drop in the total)."""
    runs = 40
    reports = [_noise_run(5000 + seed, {"south": 210.0}) for seed in range(runs)]
    found = sum(any(i.kind == "period_change" for i in r.insights) for r in reports)
    localised = sum(
        any(i.kind == "distribution_shift" and i.segments[0] == "south" for i in r.insights) for r in reports
    )
    assert found >= 0.85 * runs and localised >= 0.9 * runs


def test_mix_shift_without_a_total_change():
    rng = np.random.default_rng(7)
    # north gains exactly what south loses: the total stays flat, the mix moves
    seg, takings = _segments(rng, {"north": 640.0, "south": 160.0}, noise=0.02)
    inp = E.ExplorationInput(window=WINDOW, lineage=LINEAGE, series={"qq_takings": takings},
                             segments={"qq_takings": {"zone": seg}})
    report = E.explore(inp)
    shift = next(i for i in report.insights if i.kind == "distribution_shift")
    assert set(shift.segments) >= {"north", "south"}
    assert "flat overall" in shift.statement
    assert all(i.kind != "period_change" for i in report.insights)


def test_co_movement_is_an_association_and_lineage_pairs_are_skipped():
    rng = np.random.default_rng(11)
    shock = rng.normal(size=len(DAYS))
    a = {d: 1000 + 60 * s + 10 * rng.normal() for d, s in zip(DAYS, shock, strict=True)}
    b = {d: 400 + 25 * s + 5 * rng.normal() for d, s in zip(DAYS, shock, strict=True)}
    inp = E.ExplorationInput(window=WINDOW, lineage=LINEAGE, series={"qq_outlay": a, "qq_footfall": b})
    co = [i for i in E.explore(inp).insights if i.kind == "co_movement"]
    assert co and co[0].classification == "ASSOCIATION" and co[0].stats["r"] > 0.8
    assert "not evidence that one drives the other" in co[0].statement
    # a ratio built on one of them is "explained" by lineage: never reported as co-movement
    lineage = {**LINEAGE, "qq_footfall": MetricMeta("qq_footfall", aggregation="ratio", depends_on=("qq_outlay",))}
    inp = E.ExplorationInput(window=WINDOW, lineage=lineage, series={"qq_outlay": a, "qq_footfall": b})
    assert not [i for i in E.explore(inp).insights if i.kind == "co_movement"]


def test_outstanding_segment():
    values = {"alpha": 5000.0, "beta": 400.0, "gamma": 300.0, "delta": 220.0, "eps": 180.0, "zeta": 150.0}
    out = st.outstanding_top(values)
    assert out is not None and out.top == "alpha" and out.p_value < 0.01
    # a smooth power law has no outstanding top
    smooth = {f"s{r}": 1000.0 / r for r in range(1, 9)}
    assert st.outstanding_top(smooth).p_value > 0.2  # type: ignore[union-attr]
    assert st.outstanding_top({"a": 1.0, "b": 2.0}) is None


def test_adtributor_paper_example():
    """Bhagwan et al. (NSDI 2014) §2: the device dimension, not the data centre,
    localises the $50 revenue drop, because its distribution changed."""
    hist = [0.002, 0.003, 0.001, 0.004, 0.002]
    device = st.distribution_shift({"pc": 50, "mobile": 25, "tablet": 25}, {"pc": 49, "mobile": 1, "tablet": 0}, hist)
    assert device is not None
    assert {s.segment for s in device.explaining} == {"mobile", "tablet"}
    assert device.explained == pytest.approx(0.98)
    centre = st.distribution_shift({"x": 94, "y": 6}, {"x": 47, "y": 3}, hist)
    assert centre is not None and centre.divergence == pytest.approx(0.0, abs=1e-12)
    assert device.p_value < centre.p_value


def test_primitives():
    assert st.js_term(0.3, 0.3) == pytest.approx(0.0)
    assert 0 < st.js_term(1.0, 0.0) <= 1.0
    q = st.benjamini_hochberg([0.01, 0.04, 0.03, 0.5])
    assert q == pytest.approx([0.04, 0.16 / 3, 0.16 / 3, 0.5])  # step-up: q(0.03) = min(0.06, q(0.04))
    t, df = st.prediction_t(10.0, [1.0, 2.0, 1.5, 1.2, 1.8])  # type: ignore[misc]
    assert df == 4 and st.t_tail(t, df, two_sided=True) > 1e-5  # five windows can't make a result near-certain
    assert st.prediction_t(5.0, [1.0, 1.0]) is None


def test_windows_tile_backwards():
    wins = E.windows(WINDOW, 2)
    assert wins[0] == WINDOW
    assert wins[1][-1] == WINDOW[0] - timedelta(days=1) and len(wins[1]) == 7
    assert wins[2][-1] == wins[1][0] - timedelta(days=1)

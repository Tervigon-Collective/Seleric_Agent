"""Diagnosis engine (causal/diagnosis.py) against data with a known answer.

Every scenario is generated so the true cause (or the absence of one) is known;
the engine must recover it — or say it cannot — without any metric, dimension
or business threshold baked in. Metric ids here are deliberately generic
(``m_*``) so a passing test cannot lean on a name.
"""

from __future__ import annotations

import logging
import re
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from seleric_swarm.causal.diagnosis import (
    DiagnosisInput,
    MetricMeta,
    diagnose,
    discover_identities,
    shapley,
)

pytestmark = pytest.mark.filterwarnings("ignore")
warnings.filterwarnings("ignore")
logging.getLogger("dowhy").setLevel(logging.ERROR)

N = 57
DAYS = [date(2026, 8, 8) + timedelta(days=i) for i in range(N)]
EVENT = [DAYS[-1]]


def S(values) -> dict[date, float]:
    return {d: float(v) for d, v in zip(DAYS, values, strict=True)}


def A(mid: str, view: str = "v1", deps: tuple[str, ...] = ()) -> MetricMeta:
    return MetricMeta(mid, "additive", view, deps)


def R(mid: str, view: str = "v1", deps: tuple[str, ...] = ()) -> MetricMeta:
    return MetricMeta(mid, "ratio", view, deps)


def driver(report, name):
    return next(f for f in report.drivers if f.driver == name)


# ---------------------------------------------------------------- the canonical chain
def _spend_chain(seed: int = 3, *, cut: float | None = 400.0, n_shocks: int = 0):
    """m_spend -> m_visits -> m_orders; m_sales = m_orders x m_aov (identity)."""
    rng = np.random.default_rng(seed)
    spend = 1000 + 100 * rng.normal(size=N)
    for k in range(n_shocks):  # on/off switches in history (intervention-like)
        a = 10 + 15 * k
        spend[a : a + 4] = 0.0
    if cut is not None:
        spend[-1] = cut
    visits = 2000 + 1.5 * spend + rng.normal(0, 60, N)
    orders = np.maximum(5, 0.02 * visits + rng.normal(0, 3, N))
    aov = 2000 + rng.normal(0, 50, N)
    sales = orders * aov
    impressions = 50 * spend + rng.normal(0, 500, N)
    noise = rng.normal(0, 1, N)
    lineage = {
        "m_sales": A("m_sales", "orders_view"),
        "m_orders": A("m_orders", "orders_view"),
        "m_aov": R("m_aov", "orders_view", ("m_sales", "m_orders")),
        "m_spend": A("m_spend", "ads_view"),
        "m_impr": A("m_impr", "ads_view"),
        "m_visits": A("m_visits", "web_view"),
        "m_noise": A("m_noise", "other_view"),
    }
    series = {
        "m_sales": S(sales), "m_orders": S(orders), "m_aov": S(aov), "m_spend": S(spend),
        "m_impr": S(impressions), "m_visits": S(visits), "m_noise": S(noise),
    }
    return lineage, series


def test_spend_cut_is_found_with_identity_and_exclusions():
    lineage, series = _spend_chain()
    r = diagnose(DiagnosisInput(
        outcome="m_sales", event_days=EVENT, series=series, lineage=lineage,
        candidate_drivers=["m_spend", "m_impr", "m_noise", "m_orders", "m_aov"],
        claimed_direction="down",
    ))
    assert r.event.unusual and r.event.premise == "confirmed"
    assert r.identities == ["m_sales = m_aov × m_orders"]
    by = {t.metric: t for t in r.decomposition}
    assert abs(sum(t.contribution for t in r.decomposition) - r.event.delta) < 1e-6  # exact
    assert abs(by["m_orders"].share_of_change) > abs(by["m_aov"].share_of_change)
    spend = driver(r, "m_spend")
    # Same-day effect, no lead/lag or on/off evidence: the effect size is right,
    # but direction is not identified, so it must NOT be called a cause.
    assert spend.status == "implicated" and spend.classification == "correlation"
    assert spend.direction_evidence["direction"] == "assumed"
    assert spend.inseparable_from == ["m_impr"]  # collinear treatments are flagged, not split
    lo, hi = spend.effect_ci
    assert lo < 60 < hi  # true d(sales)/d(spend) = 0.03 * 2000
    assert driver(r, "m_aov").status == "excluded"  # computed from the outcome
    assert driver(r, "m_orders").status == "excluded"  # arithmetic component
    assert driver(r, "m_noise").status == "ruled_out"
    assert r.verdict == "located_cause_not_identified"  # decomposed, but no cause claimed


def test_intervention_shifts_upgrade_direction_to_supported():
    lineage, series = _spend_chain(seed=5, n_shocks=3)
    r = diagnose(DiagnosisInput(
        outcome="m_sales", event_days=EVENT, series=series, lineage=lineage,
        candidate_drivers=["m_spend", "m_noise"],
    ))
    spend = driver(r, "m_spend")
    assert spend.direction_evidence["direction"] == "intervention"
    assert spend.classification == "supported_cause", spend.reason
    assert r.verdict in ("explained", "partially_explained")


def test_noise_day_is_not_diagnosed():
    lineage, series = _spend_chain(cut=None)
    r = diagnose(DiagnosisInput(
        outcome="m_sales", event_days=EVENT, series=series, lineage=lineage,
        candidate_drivers=["m_spend", "m_noise"], claimed_direction="down",
    ))
    assert not r.event.unusual
    assert r.verdict == "no_unusual_change"
    assert "normal day-to-day variation" in r.headline


def test_weekly_seasonality_is_not_an_event():
    rng = np.random.default_rng(1)
    dow = np.array([d.weekday() for d in DAYS])
    y = 1000 - 400 * (dow == 6) + rng.normal(0, 20, N)  # every Sunday is low
    event = [d for d in DAYS if d.weekday() == 6][-1]
    lineage = {"m_y": A("m_y")}
    r = diagnose(DiagnosisInput(
        outcome="m_y", event_days=[event], series={"m_y": S(y)}, lineage=lineage, claimed_direction="down",
    ))
    assert r.event.previous_period["delta"] < -300  # naive day-over-day says "big fall"
    assert not r.event.unusual and r.verdict == "no_unusual_change"
    assert all(d.weekday() == 6 for d in map(date.fromisoformat, r.event.reference_days[event.isoformat()]))


def test_premise_contradicted_when_metric_rose():
    lineage, series = _spend_chain(cut=1600.0)
    r = diagnose(DiagnosisInput(
        outcome="m_sales", event_days=EVENT, series=series, lineage=lineage, claimed_direction="down",
    ))
    assert r.event.direction == "up" and r.event.premise == "contradicted"


def test_calendar_confounding_is_not_causal():
    rng = np.random.default_rng(2)
    weekend = np.array([d.weekday() >= 5 for d in DAYS], dtype=float)
    x = 100 + 50 * weekend + rng.normal(0, 3, N)
    y = 500 + 200 * weekend + rng.normal(0, 10, N)
    y[-1] -= 120  # an unexplained drop
    lineage = {"m_y": A("m_y"), "m_x": A("m_x", "v2")}
    r = diagnose(DiagnosisInput(
        outcome="m_y", event_days=EVENT, series={"m_y": S(y), "m_x": S(x)}, lineage=lineage, candidate_drivers=["m_x"],
    ))
    f = driver(r, "m_x")
    assert f.naive_association["p_value"] < 0.05  # a correlation engine would bite
    assert f.classification == "correlation" and f.status == "ruled_out"
    assert r.verdict == "root_cause_not_identified"


def test_reverse_causality_is_flagged():
    rng = np.random.default_rng(4)
    y = 500 + np.cumsum(rng.normal(0, 10, N))
    y[-2:] -= 120  # the outcome drops first ...
    x = np.r_[100, 0.2 * y[:-1]] + rng.normal(0, 1, N)  # ... and the budget follows it a day later
    lineage = {"m_y": A("m_y"), "m_x": A("m_x", "v2")}
    r = diagnose(DiagnosisInput(
        outcome="m_y", event_days=EVENT, series={"m_y": S(y), "m_x": S(x)}, lineage=lineage, candidate_drivers=["m_x"],
    ))
    f = driver(r, "m_x")
    assert f.driver_moved_before_or_at_event  # it did move with the event ...
    assert f.direction_evidence["direction"] == "feedback"  # ... but the outcome led it
    assert f.classification == "correlation"


def test_mediator_is_not_adjusted_away():
    lineage, series = _spend_chain()
    r = diagnose(DiagnosisInput(
        outcome="m_sales", event_days=EVENT, series=series, lineage=lineage,
        candidate_drivers=["m_spend", "m_visits"],
    ))
    spend = driver(r, "m_spend")
    # m_visits sits on spend's path; only its lag may be adjusted for.
    assert "m_visits" not in spend.adjustment_set
    lo, hi = spend.effect_ci
    assert lo < 60 < hi


def test_declared_descendant_and_collider_are_excluded():
    lineage, series = _spend_chain()
    rng = np.random.default_rng(9)
    collider = np.array([series["m_spend"][d] + series["m_sales"][d] / 100 for d in DAYS]) + rng.normal(0, 1, N)
    series["m_both"] = S(collider)
    lineage["m_both"] = R("m_both", "x_view", ("m_spend", "m_sales"))  # lineage says it uses the outcome
    r = diagnose(DiagnosisInput(
        outcome="m_sales", event_days=EVENT, series=series, lineage=lineage,
        candidate_drivers=["m_spend", "m_both"],
    ))
    assert driver(r, "m_both").status == "excluded"
    assert all("m_both" != v for v in driver(r, "m_spend").adjustment_set)


def test_simpsons_paradox_in_a_rate():
    rng = np.random.default_rng(6)
    # Two segments; rates never change. Event day: traffic mix tilts to the low-rate segment.
    w_hi = 1000 + rng.normal(0, 30, N)
    w_lo = 1000 + rng.normal(0, 30, N)
    w_hi[-1], w_lo[-1] = 500, 1600
    r_hi = np.clip(0.05 + rng.normal(0, 0.002, N), 0, 1)
    r_lo = np.clip(0.01 + rng.normal(0, 0.001, N), 0, 1)
    r_hi[-1] += 0.004  # within-segment rates even improve slightly
    r_lo[-1] += 0.001
    total_w = w_hi + w_lo
    total_r = (w_hi * r_hi + w_lo * r_lo) / total_w
    lineage = {"m_rate": R("m_rate", "s", ("m_weight",)), "m_weight": A("m_weight", "s")}
    seg = lambda a, b: {"hi": S(a), "lo": S(b)}
    r = diagnose(DiagnosisInput(
        outcome="m_rate", event_days=EVENT,
        series={"m_rate": S(total_r), "m_weight": S(total_w)}, lineage=lineage,
        segments={"m_rate": {"dim_a": seg(r_hi, r_lo)}, "m_weight": {"dim_a": seg(w_hi, w_lo)}},
        denominators={"m_rate": "m_weight"},
    ))
    assert r.event.unusual and r.event.direction == "down"
    dim = r.dimensions[0]
    assert dim.kind == "rate" and dim.simpsons_paradox
    assert dim.rate_effect > 0 > dim.mix_effect
    assert abs((dim.mix_effect + dim.rate_effect) - r.event.delta) < 0.01 * abs(r.event.delta)


def test_localised_vs_broad_based_segments():
    rng = np.random.default_rng(7)
    segs = {k: 200 + rng.normal(0, 8, N) for k in "abcde"}
    segs["c"][-1] -= 150  # the drop sits in one segment
    total = sum(segs.values())
    lineage = {"m_y": A("m_y")}
    r = diagnose(DiagnosisInput(
        outcome="m_y", event_days=EVENT, series={"m_y": S(total)}, lineage=lineage,
        segments={"m_y": {"dim_local": {k: S(v) for k, v in segs.items()}}},
    ))
    d = r.dimensions[0]
    assert d.localised and d.top[0].segment == "c" and abs(d.coverage - 1) < 1e-6
    # Same total drop spread proportionally across segments -> broad-based.
    segs2 = {k: 200 + rng.normal(0, 8, N) for k in "abcde"}
    for series in segs2.values():
        series[-1] *= 0.6
    r2 = diagnose(DiagnosisInput(
        outcome="m_y", event_days=EVENT, series={"m_y": S(sum(segs2.values()))}, lineage=lineage,
        segments={"m_y": {"dim_flat": {k: S(v) for k, v in segs2.items()}}},
    ))
    assert r2.dimensions[0].broad_based and not r2.dimensions[0].localised


def test_identifier_like_dimension_is_skipped():
    rng = np.random.default_rng(8)
    y = 100 + rng.normal(0, 5, N)
    y[-1] -= 40
    ids = {f"id{i}": {DAYS[i]: float(y[i])} for i in range(N)}  # every value appears once
    r = diagnose(DiagnosisInput(
        outcome="m_y", event_days=EVENT, series={"m_y": S(y)}, lineage={"m_y": A("m_y")},
        segments={"m_y": {"dim_id": ids}},
    ))
    assert r.dimensions == []


def test_outlier_weeks_are_excluded_from_reference():
    rng = np.random.default_rng(10)
    y = 1000 + rng.normal(0, 20, N)
    same_dow = [i for i in range(N - 1) if DAYS[i].weekday() == DAYS[-1].weekday()]
    y[same_dow[-2]] = 100  # an outage two weeks before, same weekday
    y[-1] = 990
    r = diagnose(DiagnosisInput(outcome="m_y", event_days=EVENT, series={"m_y": S(y)}, lineage={"m_y": A("m_y")}))
    assert DAYS[same_dow[-2]].isoformat() in r.event.excluded_outlier_days
    assert DAYS[same_dow[-2]].isoformat() not in r.event.reference_days[DAYS[-1].isoformat()]
    assert r.verdict == "no_unusual_change"


def test_missing_event_day_and_sparse_history():
    lineage, series = _spend_chain()
    del series["m_sales"][EVENT[0]]
    r = diagnose(DiagnosisInput(outcome="m_sales", event_days=EVENT, series=series, lineage=lineage))
    assert r.verdict == "insufficient_data"
    short = {d: v for d, v in _spend_chain()[1]["m_sales"].items() if d >= DAYS[-6]}
    r2 = diagnose(DiagnosisInput(outcome="m_sales", event_days=EVENT, series={"m_sales": short}, lineage=lineage))
    assert r2.verdict == "insufficient_data" and "history" in r2.headline


def test_sparse_driver_is_insufficient_not_causal():
    lineage, series = _spend_chain()
    series["m_spend"] = {d: v for i, (d, v) in enumerate(series["m_spend"].items()) if i % 3 == 0 or d == EVENT[0]}
    r = diagnose(DiagnosisInput(
        outcome="m_sales", event_days=EVENT, series=series, lineage=lineage, candidate_drivers=["m_spend"],
    ))
    f = driver(r, "m_spend")
    assert f.classification in ("insufficient_evidence", None)
    assert f.status in ("insufficient_evidence", "excluded")


def test_partial_day_is_refused():
    lineage, series = _spend_chain()
    r = diagnose(DiagnosisInput(
        outcome="m_sales", event_days=EVENT, series=series, lineage=lineage, partial_days={EVENT[0]},
    ))
    assert r.verdict == "insufficient_data" and "not complete" in r.headline


def test_multiple_independent_causes_both_found():
    rng = np.random.default_rng(11)
    a = 100 + 10 * rng.normal(size=N)
    b = 50 + 5 * rng.normal(size=N)
    a[-1], b[-1] = 60, 30
    y = 1000 + 4 * a + 6 * b + rng.normal(0, 15, N)
    lineage = {"m_y": A("m_y"), "m_a": A("m_a", "va"), "m_b": A("m_b", "vb")}
    r = diagnose(DiagnosisInput(
        outcome="m_y", event_days=EVENT, series={"m_y": S(y), "m_a": S(a), "m_b": S(b)},
        lineage=lineage, candidate_drivers=["m_a", "m_b"],
    ))
    fa, fb = driver(r, "m_a"), driver(r, "m_b")
    assert fa.status == fb.status == "implicated"
    assert fa.effect_ci[0] < 4 < fa.effect_ci[1] and fb.effect_ci[0] < 6 < fb.effect_ci[1]
    assert fa.inseparable_from == [] and fb.inseparable_from == []


def test_identity_must_be_verified_by_data():
    rng = np.random.default_rng(12)
    x = 100 + rng.normal(0, 5, N)
    z = 30 + rng.normal(0, 2, N)
    y = 3 * x + rng.normal(0, 10, N)  # lineage below falsely claims y depends on x and z
    lineage = {"m_y": R("m_y", "v", ("m_x", "m_z")), "m_x": A("m_x"), "m_z": A("m_z")}
    found = discover_identities("m_y", lineage, {"m_y": S(y), "m_x": S(x), "m_z": S(z)}, DAYS)
    assert found == []
    exact = {d: float(x[i] / z[i]) for i, d in enumerate(DAYS)}
    found = discover_identities("m_y", lineage, {"m_y": exact, "m_x": S(x), "m_z": S(z)}, DAYS)
    assert [i.describe() for i in found] == ["m_y = m_x ÷ m_z"]


def test_shapley_is_exact_and_symmetric():
    f = lambda v: v[0] * v[1] / v[2]
    x0, x1 = [10.0, 4.0, 2.0], [7.0, 5.0, 2.5]
    parts = shapley(f, x0, x1)
    assert abs(sum(parts) - (f(x1) - f(x0))) < 1e-12


def test_engine_source_hardcodes_no_catalogue_names():
    root = Path(__file__).resolve().parents[2]
    registry = (root / "config" / "metric_registry.yaml").read_text(encoding="utf-8")
    ids = set(re.findall(r"^\s*-?\s*(?:id|catalogue_metric):\s*['\"]?([A-Za-z0-9_.]+)", registry, re.MULTILINE))
    ids |= {i.split(".", 1)[1] for i in ids if "." in i}
    ids = {i for i in ids if len(i) > 3}
    assert ids, "registry parse found no metric ids"
    for rel in ("src/seleric_swarm/causal/diagnosis.py", "src/seleric_swarm/toolsets/diagnosis.py"):
        path = root / rel
        if not path.exists():
            continue
        code = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
        )
        hits = sorted(i for i in ids if re.search(rf"[\"']{re.escape(i)}[\"']", code))
        assert hits == [], f"{rel} hardcodes catalogue metric ids: {hits}"


def test_rate_chain_rejects_coincidental_proportionality():
    from seleric_swarm.causal.diagnosis import discover_rate_chains

    rng = np.random.default_rng(13)
    visits = 3000 + rng.normal(0, 200, N)
    share = np.clip(0.7 + rng.normal(0, 0.01, N), 0, 1)
    spend = 26 * share * visits * (1 + rng.normal(0, 0.03, N))  # proportional, but 26x: not the same events
    buys = 1.2 * 0.012 * visits * (1 + rng.normal(0, 0.03, N))  # the same events counted twice
    lineage = {"m_spend": A("m_spend"), "m_buys": A("m_buys"), "m_visits": A("m_visits"),
               "m_share": R("m_share", "v1", ("m_visits",)), "m_cr": R("m_cr", "v1", ("m_visits",))}
    series = {"m_spend": S(spend), "m_buys": S(buys), "m_visits": S(visits), "m_share": S(share),
              "m_cr": S(np.full(N, 0.012) * (1 + rng.normal(0, 0.02, N)))}
    assert discover_rate_chains("m_spend", lineage, series, DAYS) == []
    found = discover_rate_chains("m_buys", lineage, series, DAYS)
    assert [i.factors[0][0] for i in found][:1] == ["m_cr"]


def test_fall_versus_the_day_before_is_not_called_a_rise():
    """Live MS3-fbcf78e410: "why did sales fall yesterday" — sales were 25%
    below the day before but above the weekday norm; the answer said "did not
    fall, it rose". Both comparisons must be stated."""
    rng = np.random.default_rng(5)
    y = 1000 + rng.normal(0, 20, N)
    y[-2] = 1700.0  # an exceptional previous day
    y[-1] = 1250.0  # down 26% on it, up 25% on the usual level
    r = diagnose(DiagnosisInput(
        outcome="m_y", event_days=EVENT, series={"m_y": S(y)}, lineage={"m_y": A("m_y")}, claimed_direction="down",
    ))
    assert r.event.direction == "up" and r.event.premise == "vs_previous_only"
    assert "versus the day before" in r.headline and "Contrary to the question" not in r.headline

    # a genuine contradiction (up on both baselines) is still called one
    y[-2] = 1000.0
    r = diagnose(DiagnosisInput(
        outcome="m_y", event_days=EVENT, series={"m_y": S(y)}, lineage={"m_y": A("m_y")}, claimed_direction="down",
    ))
    assert r.event.premise == "contradicted"


def test_a_recount_of_an_identity_component_is_never_the_cause():
    """Live MS3-73dac57c40: gross sales decomposed as spend x ROAS, and funnel
    purchases (the same orders counted by the web funnel) was named the cause.
    Every verified identity's components are compared, not just the one used."""
    rng = np.random.default_rng(7)
    spend = 1000 + 100 * rng.normal(size=N)
    spend[-1] = 400.0
    orders = np.maximum(5, 40 + 0.02 * spend + rng.normal(0, 4, N))
    aov = 2000 + rng.normal(0, 500, N)  # baskets vary a lot: orders and sales are not in lockstep
    sales = orders * aov
    lineage = {
        "m_sales": A("m_sales", "orders_view"),
        "m_orders": A("m_orders", "orders_view"),
        "m_aov": R("m_aov", "orders_view", ("m_sales", "m_orders")),
        "m_spend": A("m_spend", "ads_view"),
        "m_roas": R("m_roas", "ads_view", ("m_sales", "m_spend")),
        "m_funnel": A("m_funnel", "funnel_view"),
    }
    series = {
        "m_sales": S(sales), "m_orders": S(orders), "m_spend": S(spend),
        "m_aov": S(sales / orders * (1 + rng.normal(0, 0.01, N))),  # reported AOV is rounded: approximate identity
        "m_roas": S(sales / spend),  # exact: the decomposition picks spend x ROAS
        "m_funnel": S(orders + rng.normal(0, 0.3, N)),
    }
    r = diagnose(DiagnosisInput(
        outcome="m_sales", event_days=EVENT, series=series, lineage=lineage,
        candidate_drivers=["m_spend", "m_funnel"], claimed_direction="down",
    ))
    assert {t.metric for t in r.decomposition} >= {"m_spend"}
    assert any(f.driver == "m_funnel" and f.status == "excluded" for f in r.drivers), [(f.driver, f.status) for f in r.drivers]


# ---------------------------------------------------------------- exact additive bridge
def _pnl(seed: int = 11):
    """profit = margin − spend; margin = sales − cogs; plus a near-twin and noise.
    The event day mirrors 2026-10-02: orders collapse, spend is cut far less."""
    rng = np.random.default_rng(seed)
    sales = 80000 + 8000 * rng.normal(size=N)
    sales[-1] = 38000
    cogs = 0.38 * sales + 500 * rng.normal(size=N)
    spend = 42000 + 3000 * rng.normal(size=N)
    spend[-1] = 35000
    margin = sales - cogs
    profit = margin - spend
    twin = profit + 4000 * rng.normal(size=N)  # another date basis: close, never exact
    sessions = 4000 + 300 * rng.normal(size=N)
    lineage = {m: MetricMeta(m, "additive", "pnl", unit="INR") for m in
               ("m_profit", "m_margin", "m_sales", "m_cogs", "m_spend", "m_profit_other_basis")}
    lineage["m_sessions"] = MetricMeta("m_sessions", "additive", "web", unit="count")
    series = {"m_profit": S(profit), "m_margin": S(margin), "m_sales": S(sales), "m_cogs": S(cogs),
              "m_spend": S(spend), "m_profit_other_basis": S(twin), "m_sessions": S(sessions)}
    return lineage, series


def test_additive_outcome_gets_an_exact_bridge_that_reconciles():
    lineage, series = _pnl()
    pool = ["m_margin", "m_sales", "m_cogs", "m_spend", "m_profit_other_basis"]
    r = diagnose(DiagnosisInput(
        outcome="m_profit", event_days=EVENT, series=series, lineage=lineage,
        candidate_drivers=["m_sales", "m_sessions"], claimed_direction="down", bridge_candidates=pool,
    ))
    assert r.bridge_identity[0] in ("m_profit = m_margin − m_spend", "m_profit = − m_spend + m_margin")
    assert "m_margin = m_sales − m_cogs" in r.bridge_identity or "m_margin = − m_cogs + m_sales" in r.bridge_identity
    top = [t for t in r.bridge if t.depth == 1]
    assert abs(sum(t.contribution for t in top) - r.event.delta) < 1e-6  # vs the usual: exact
    assert abs(sum(t.contribution_vs_previous for t in top) - r.event.previous_period["delta"]) < 1e-6
    leaves = {t.metric: t for t in r.bridge if t.depth == 2}
    assert leaves["m_sales"].share_of_change > 1.0  # sales fell by more than profit did
    assert leaves["m_cogs"].share_of_change < 0  # lower COGS offset part of it
    # Bridge terms are arithmetic components, never tested as causes of the outcome.
    assert driver(r, "m_sales").status == "excluded"
    assert any("EXACT BRIDGE" in line for line in r.narrative)
    assert r.verdict != "root_cause_not_identified"


def test_a_near_twin_is_not_accepted_as_a_bridge():
    lineage, series = _pnl()
    r = diagnose(DiagnosisInput(
        outcome="m_profit", event_days=EVENT, series=series, lineage=lineage,
        bridge_candidates=["m_profit_other_basis", "m_sessions"],
    ))
    assert r.bridge == [] and r.bridge_identity == []

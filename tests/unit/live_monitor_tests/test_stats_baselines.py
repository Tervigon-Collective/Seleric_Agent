from __future__ import annotations

import itertools
import math

import pytest

from seleric_swarm.services.live_monitor import baselines as B
from seleric_swarm.services.live_monitor import guardrails as G
from seleric_swarm.services.live_monitor.core import stats as S


def test_cpa_probability_is_monotone_in_purchases():
    ps = [S.prob_cpa_above(1200, k, 6000, 1 / 1221, 2) for k in range(10)]
    assert all(a >= b for a, b in itertools.pairwise(ps))
    assert ps[0] > 0.95 and ps[-1] < 0.2


def test_prior_dominates_tiny_samples():
    # 1 purchase on 300 rupees is a raw CPA of 300; with the prior it stays near the parent's CPA.
    p = S.prob_cpa_above(1200, 1, 300, 1 / 1221, 2)
    assert 0.3 < p < 0.8


def test_roas_and_zero_probability():
    assert S.prob_roas_below(2.2, 1, 6000, 2600, 1 / 1221, 2665, 2) > 0.9
    assert S.prob_roas_below(2.2, 30, 20000, 80000, 1 / 1221, 2665, 2) < 0.05
    assert S.prob_zero(1 / 1221, 0) == 1.0
    assert math.isclose(S.prob_zero(0.001, 3000), math.exp(-3))


def test_beta_and_quantiles():
    assert S.prob_p_below(0.0112, 25, 5000, 0.0166, 2000) > 0.95
    assert S.prob_p_below(0.0112, 80, 5000, 0.0166, 2000) < 0.05
    assert S.quantiles([]) is None
    assert S.quantiles([5.0]) == (5.0, 5.0, 5.0)
    assert S.quantiles(list(map(float, range(11)))) == (1.0, 5.0, 9.0)


def _history():
    rows = []
    for d in range(1, 15):
        day = f"2026-11-{d:02d}"
        for ad, adset, spend, k in (("a1", "s1", 1000.0, 1), ("a2", "s1", 500.0, d % 2)):
            rows.append({"date": day, "ad_id": ad, "adset_id": adset, "campaign_id": "c1", "spend": spend + d * 10,
                         "impressions": 2000.0, "link_clicks": 30.0, "lpv": 28.0, "purchases": float(k),
                         "purchase_value": 2600.0 * k, "atc": 2.0, "ic": 1.0})
    return rows


def test_baselines_per_level_pooled_and_weekday():
    hourly = {h: {"spend": 100.0 if 8 <= h < 23 else 10.0, "impressions": 1000.0} for h in range(24)}
    rows = B.compute(_history(), hourly, window_days=14, weekday_aware=True, curve_days=28, account_id="act_1")
    by = {(r["level"], r["entity_id"], r["metric"], r["weekday"]): r for r in rows}
    assert by[("ad", "a1", "spend", None)]["n"] == 14
    rate = by[("adset", "s1", "purchases_per_inr", None)]["mean"]
    spend = sum(r["spend"] for r in _history())
    purchases = sum(r["purchases"] for r in _history())
    assert rate == pytest.approx(purchases / spend)
    assert ("account", "act_1", "spend", 4) in by  # weekday rows for account and campaign
    assert not any(k[0] == "ad" and k[3] is not None for k in by)
    curve = by[("account", "act_1", "curve_spend", None)]["extra"]["curve"]
    assert len(curve) == 24 and curve[-1] == 1.0 and all(a <= b for a, b in itertools.pairwise(curve))


def test_ratio_baseline_skips_days_without_denominator():
    rows = B.compute(_history(), None, window_days=14, weekday_aware=False, curve_days=28, account_id="act_1")
    cpa = next(r for r in rows if r["level"] == "ad" and r["entity_id"] == "a2" and r["metric"] == "cpa")
    assert cpa["n"] == 7  # only the days with a purchase


def test_guardrails_from_finance():
    finance = {"rows": [{"be_roas": 1.71, "contribution_margin_pct": 0.585, "net_aov": 2045.0}]}
    g = G.compute(finance, meta_aov=2665.0, target_multiple=1.3, computed_at="now")
    assert g["be_roas_platform"] == pytest.approx(1.71 * 2665 / 2045, abs=0.01)
    assert g["allowable_cpa"] == pytest.approx(2045 * 0.585, abs=1)
    assert g["target_roas"] == pytest.approx(g["be_roas_platform"] * 1.3, abs=0.01)
    assert G.compute({"rows": []}, 2665.0, 1.3, "now") is None


def test_tracking_reference_median():
    pixel = {f"2026-11-{d:02d}": {"purchases": 60.0, "atc": 100.0, "spend": 1.0} for d in range(1, 11)}
    shop = {"rows": [{"report_date.day": f"2026-11-{d:02d}", "ssot_campaign_orders": 30} for d in range(1, 11)]}
    ref = G.tracking_reference(pixel, shop)
    assert ref["median_ratio"] == 2.0 and len(ref["days"]) == 10

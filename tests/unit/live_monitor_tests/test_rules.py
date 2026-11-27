from __future__ import annotations

from seleric_swarm.services.live_monitor.core.context import Band
from seleric_swarm.services.live_monitor.core.registry import RULES, ensure_loaded

from .helpers import ADSET, CAMPAIGN, NOW, ctx, m, world

ensure_loaded()


def run(rule_id: str, snap, entity_id: str, **ctx_kw):
    c = ctx(snap, **ctx_kw)
    rule = RULES[rule_id]
    return rule.fn(c, snap.get(entity_id), rule.params())


AD = "120251540306860783"
ACC = "act_1685189008684458"


# --------------------------------------------------------------------------------------------- performance

def test_pacing_flags_fast_spend_and_skips_small_expectation():
    fast = world(metrics={"campaign": {"today": m(spend=1100)}})
    hit = run("spend_pacing", fast, CAMPAIGN)
    assert hit.severity == "critical" and "pause" in hit.actions
    assert hit.evidence["expected_by_now"] == 625  # 1000 x 15/24, linear without a curve
    early = run("spend_pacing", fast, CAMPAIGN, now=NOW.replace(hour=8))
    assert early is None  # 1000 x 8/24 = 333 < 500 minimum


def test_pacing_uses_the_hour_curve_when_present():
    curve = [round((h + 1) / 24, 6) for h in range(24)]
    curve = [min(1.0, x * 1.5) for x in curve]  # front-loaded day: 94 % done by 15:00
    snap = world(metrics={"campaign": {"today": m(spend=1100)}})
    hit = run("spend_pacing", snap, CAMPAIGN, curves={"spend": curve})
    assert hit is None


def test_under_pacing_after_noon():
    snap = world(metrics={"campaign": {"today": m(spend=200)}})
    hit = run("spend_pacing", snap, CAMPAIGN)
    assert hit.severity == "warning" and "Under-delivering" in hit.summary


def test_spend_no_purchase_needs_unlikely_zero():
    assert run("spend_no_purchase", world(metrics={"ad": {"today": m(spend=2000)}}), AD) is None  # p0 = 0.19
    warn = run("spend_no_purchase", world(metrics={"ad": {"today": m(spend=3000)}}), AD)
    assert warn.severity == "warning" and warn.evidence["p_zero_at_usual_rate"] < 0.1
    crit = run("spend_no_purchase", world(metrics={"ad": {"today": m(spend=4000)}}), AD)
    assert crit.severity == "critical"
    assert run("spend_no_purchase", world(metrics={"ad": {"today": m(spend=4000, purchases=1)}}), AD) is None


def test_cpa_above_allowable_three_and_seven_days():
    bad = world(metrics={"ad": {"3d": m(spend=6000, purchases=1), "7d": m(spend=9000, purchases=2)}})
    hit = run("cpa_above_allowable", bad, AD)
    assert hit.severity == "critical" and hit.confidence >= 0.8
    assert hit.evidence["min_spend_ok"] is True and hit.evidence["confidence"] >= 0.8
    recovering = world(metrics={"ad": {"3d": m(spend=6000, purchases=1), "7d": m(spend=9000, purchases=9)}})
    assert run("cpa_above_allowable", recovering, AD).severity == "warning"
    fine = world(metrics={"ad": {"3d": m(spend=6000, purchases=6)}})
    assert run("cpa_above_allowable", fine, AD) is None
    small = world(metrics={"ad": {"3d": m(spend=1500, purchases=0)}})
    assert run("cpa_above_allowable", small, AD) is None  # under 2 x allowable


def test_confidence_boundary_at_stats_default():
    """At exactly stats.confidence (0.8) the CPA gate fires; just under it stays quiet."""
    from seleric_swarm.services.live_monitor.core import stats as S
    # Same inputs the rule uses: spend 6000, purchases 2 → p_above just at/near the line for allowable 1221
    p = S.prob_cpa_above(1221, 2, 6000, 1 / 1221, 2)
    assert p >= 0.8
    hit = run("cpa_above_allowable", world(metrics={"campaign": {"3d": m(spend=6000, purchases=2),
                                                                  "7d": m(spend=12000, purchases=8)}}), CAMPAIGN)
    assert hit is not None and hit.confidence >= 0.8
    soft = run("cpa_above_allowable", world(metrics={"campaign": {"3d": m(spend=6000, purchases=5),
                                                                  "7d": m(spend=12000, purchases=12)}}), CAMPAIGN)
    assert soft is None


def test_one_lucky_purchase_is_not_judged_on_raw_cpa():
    # 1 purchase on 300: a raw CPA of 300 would look brilliant; the prior keeps it unjudged.
    snap = world(metrics={"campaign": {"3d": m(spend=300, purchases=1, purchase_value=2600)},
                          "ad": {"3d": m(spend=300, purchases=1, purchase_value=2600)}})
    assert run("roas_below", snap, CAMPAIGN) is None and run("cpa_above_allowable", snap, AD) is None


def test_roas_warning_below_target_critical_below_break_even():
    crit = world(metrics={"campaign": {"3d": m(spend=6000, purchases=1, purchase_value=2600),
                                       "7d": m(spend=9000, purchases=2, purchase_value=5200)}})
    hit = run("roas_below", crit, CAMPAIGN)
    assert hit.severity == "critical" and hit.evidence["be_roas"] == 2.2
    warn = world(metrics={"campaign": {"3d": m(spend=20000, purchases=18, purchase_value=46000),
                                       "7d": m(spend=40000, purchases=40, purchase_value=100000)}})
    hit = run("roas_below", warn, CAMPAIGN)
    assert hit.severity == "warning" and hit.evidence["target_roas"] == 2.9
    good = world(metrics={"campaign": {"3d": m(spend=20000, purchases=30, purchase_value=80000)}})
    assert run("roas_below", good, CAMPAIGN) is None


def test_cpm_spike_against_p90():
    snap = world(metrics={"campaign": {"today": m(spend=5400, impressions=6000)}})  # CPM 900 vs p90 686
    hit = run("cpm_spike", snap, CAMPAIGN)
    assert hit.severity == "warning" and hit.impact_inr > 0
    assert run("cpm_spike", world(metrics={"campaign": {"today": m(spend=3600, impressions=6000)}}), CAMPAIGN) is None


def test_ctr_drop_on_ad():
    snap = world(metrics={"ad": {"today": m(impressions=5000, link_clicks=25)}})
    assert run("ctr_drop", snap, AD).severity == "warning"
    assert run("ctr_drop", world(metrics={"ad": {"today": m(impressions=5000, link_clicks=80)}}), AD) is None


def test_creative_fatigue():
    snap = world(metrics={"ad": {"7d": m(frequency=2.6), "3d": m(impressions=5000, link_clicks=50),
                                 "prev7d": m(impressions=10000, link_clicks=150)}})
    hit = run("creative_fatigue", snap, AD)
    assert hit.severity == "critical" and hit.evidence["ctr_drop"] > 0.3
    low_freq = world(metrics={"ad": {"7d": m(frequency=1.4), "3d": m(impressions=5000, link_clicks=50),
                                     "prev7d": m(impressions=10000, link_clicks=150)}})
    assert run("creative_fatigue", low_freq, AD) is None


def test_landing_break_carries_the_url():
    snap = world(metrics={"campaign": {"today": m(link_clicks=300, lpv=120, spend=3000)}})
    hit = run("landing_break", snap, CAMPAIGN)
    assert hit.severity == "critical" and hit.evidence["url"].startswith("https://tiltingheads.com")


def test_conversion_drop_needs_normal_ctr():
    snap = world(metrics={"campaign": {"today": m(impressions=50000, link_clicks=1000, lpv=1000, purchases=3)}})
    assert run("conversion_drop", snap, CAMPAIGN) is not None
    low_ctr = world(metrics={"campaign": {"today": m(impressions=100000, link_clicks=1000, lpv=1000, purchases=3)}})
    assert run("conversion_drop", low_ctr, CAMPAIGN) is None  # the ad is the problem, not the site


def test_tracking_gap_ratio_and_atc():
    days = [{"date": f"2026-11-{d:02d}", "pixel_purchases": 60, "pixel_atc": 120, "store_orders": 30} for d in range(5, 19)]
    days.append({"date": "2026-11-19", "pixel_purchases": 60, "pixel_atc": 120, "store_orders": 12})
    hit = run("tracking_gap", world(), ACC, tracking={"days": days, "median_ratio": 2.0})
    assert hit.severity == "warning" and hit.evidence["ratio"] == 5.0
    steady = [dict(d, store_orders=30) for d in days]
    assert run("tracking_gap", world(), ACC, tracking={"days": steady, "median_ratio": 2.0}) is None
    under = [dict(d, store_orders=30, pixel_atc=5) for d in days]
    hit = run("tracking_gap", world(), ACC, tracking={"days": under, "median_ratio": 2.0})
    assert "under-firing" in hit.summary and hit.severity == "info"  # chronic ATC under-fire is info, not a page


def test_delivery_stall_uses_entity_history_only():
    rows = {("adset", ADSET, "impressions", None): Band(10000, 24000, 30000, 24000, 14, "adset 14d")}
    snap = world(hourly={ADSET: {13: {"impressions": 900, "spend": 10}}})
    hit = run("delivery_stall", snap, ADSET, rows=rows)
    assert hit.severity == "critical" and hit.evidence["expected_impressions"] == 1000
    assert run("delivery_stall", snap, ADSET) is None  # no own history: never judged on account defaults
    delivering = world(hourly={ADSET: {14: {"impressions": 50, "spend": 1}}})
    assert run("delivery_stall", delivering, ADSET, rows=rows) is None


def test_learning_limited():
    snap = world(adset={"attrs": {"learning_stage_info": {"status": "FAIL"}}})
    assert run("learning_limited", snap, ADSET).severity == "warning"
    assert run("learning_limited", world(), ADSET) is None


def test_scale_candidate():
    snap = world(adset={"attrs": {"daily_budget": 200000}},
                 metrics={"adset": {"3d": m(spend=6000, purchases=12, purchase_value=36000)}})
    hit = run("scale_candidate", snap, ADSET)
    assert hit.severity == "info" and "budget_up" in hit.actions and hit.impact_inr > 0
    learning = world(adset={"attrs": {"daily_budget": 200000, "learning_stage_info": {"status": "LEARNING"}}},
                     metrics={"adset": {"3d": m(spend=6000, purchases=12, purchase_value=36000)}})
    assert run("scale_candidate", learning, ADSET) is None


# ---------------------------------------------------------------------------------------------- compliance

def test_status_not_active():
    snap = world(ad={"effective_status": "DISAPPROVED", "attrs": {"issues": ["Policy: misleading claims"]}})
    hit = run("status_not_active", snap, AD)
    assert hit.severity == "critical" and "misleading" in hit.summary


def test_missing_label_only_on_playbook_campaigns():
    assert run("missing_campaign_label", world(campaign={"attrs": {"labels": []}}), CAMPAIGN).actions == ("set_label",)
    old = world(campaign={"name": "Diwali retargeting", "attrs": {"labels": []}})
    assert run("missing_campaign_label", old, CAMPAIGN) is None


def test_missing_ig_stream():
    snap = world(adset={"attrs": {"targeting": {"publisher_platforms": ["facebook", "instagram"], "instagram_positions": ["story", "reels"]}}})
    assert run("missing_ig_stream", snap, ADSET).actions == ("add_ig_stream",)
    advantage = world(adset={"attrs": {"targeting": {"publisher_platforms": None, "instagram_positions": None}}})
    assert run("missing_ig_stream", advantage, ADSET) is None


def test_wrong_pixel_lifecycle_attribution():
    assert run("wrong_pixel_or_event", world(adset={"attrs": {"promoted_object": {"pixel_id": "999999", "custom_event_type": "PURCHASE"}}}), ADSET).severity == "critical"
    assert run("wrong_pixel_or_event", world(), ADSET) is None
    assert run("lifecycle_restricted", world(adset={"attrs": {"existing_customer_budget_percentage": 20}}), ADSET) is not None
    assert run("attribution_mismatch", world(adset={"attrs": {"attribution_spec": [{"event_type": "CLICK_THROUGH", "window_days": 1}]}}), ADSET) is not None
    assert run("attribution_mismatch", world(), ADSET) is None


def test_display_link_or_headline():
    hit = run("missing_display_link_or_headline", world(ad={"attrs": {"display_link": None}}), AD)
    assert hit.severity == "info" and hit.actions == ("swap_creative",)
    assert run("missing_display_link_or_headline", world(), AD) is None

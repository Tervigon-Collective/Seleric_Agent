from __future__ import annotations

from datetime import timedelta

from seleric_swarm.services.live_monitor.engine import evaluate, finding_id
from seleric_swarm.services.live_monitor.runner import due_cadences, frozen_rules, reconcile

from .helpers import AD, ADSET, CAMPAIGN, NOW, C, cfg, ctx, m, world


def _only(config, *rule_ids):
    return C.validate({**config.model_dump(mode="json"), "rules": {r: {"enabled": False} for r in _all_rules() if r not in rule_ids}})


def _all_rules():
    from seleric_swarm.services.live_monitor.core.registry import RULES

    return list(RULES)


def test_rollup_keeps_the_highest_level():
    spend = m(spend=4000)
    snap = world(metrics={"campaign": {"today": spend}, "adset": {"today": spend}, "ad": {"today": spend}})
    config = _only(cfg(), "spend_no_purchase")
    ev = evaluate(ctx(snap, config=config), config, config.tenants[0], {"5m"})
    assert [(f.rule_id, f.entity.level) for f in ev.findings] == [("spend_no_purchase", "campaign")]
    assert ev.findings[0].hit.evidence["also_below_count"] == 2


def test_learning_caps_efficiency_rules_at_info():
    snap = world(adset={"attrs": {"learning_stage_info": {"status": "LEARNING"}}}, metrics={"ad": {"today": m(spend=4000)}})
    config = _only(cfg(), "spend_no_purchase")
    ev = evaluate(ctx(snap, config=config), config, config.tenants[0], {"5m"})
    assert ev.findings[0].hit.severity == "info" and ev.findings[0].hit.evidence["learning"] is True


def test_severity_override_and_disable():
    snap = world(campaign={"attrs": {"labels": []}})
    base = _only(cfg(), "missing_campaign_label")
    raised = C.validate({**base.model_dump(mode="json"), "rules": {**base.model_dump(mode="json")["rules"],
                                                                  "missing_campaign_label": {"severity": "critical"}}})
    ev = evaluate(ctx(snap, config=raised), raised, raised.tenants[0], {"5m"})
    assert ev.findings[0].hit.severity == "critical"
    off = C.validate({**base.model_dump(mode="json"), "rules": {**base.model_dump(mode="json")["rules"],
                                                               "missing_campaign_label": {"enabled": False}}})
    assert evaluate(ctx(snap, config=off), off, off.tenants[0], {"5m"}).findings == []


def test_custom_rule_runs_with_refs():
    base = _only(cfg())
    rule = {"id": "big_spender", "title": "Ad spent over 1.5x allowable today", "platform": "meta", "level": "ad",
            "conditions": [{"field": "metrics.today.spend", "op": "gt", "value": {"ref": "guardrail.allowable_cpa", "multiply": 1.5}}]}
    config = C.validate({**base.model_dump(mode="json"), "custom_rules": [rule]})
    snap = world(metrics={"ad": {"today": m(spend=2000)}})
    ev = evaluate(ctx(snap, config=config), config, config.tenants[0], {"5m"})
    assert [f.rule_id for f in ev.findings] == ["custom.big_spender"]
    assert ev.findings[0].entity.id == AD


def test_due_cadences():
    assert due_cadences({}, NOW) == {"5m", "1h", "daily"}
    recent = {"1h": (NOW - timedelta(minutes=20)).isoformat(), "daily": NOW.isoformat()}
    assert due_cadences(recent, NOW) == {"5m"}
    assert "daily" not in due_cadences({}, NOW.replace(hour=9))


def test_reconcile_lifecycle_with_cooldown():
    snap = world(metrics={"campaign": {"today": m(spend=4000)}})
    config = _only(cfg(), "spend_no_purchase")
    ev = evaluate(ctx(snap, config=config), config, config.tenants[0], {"5m"})
    fid = finding_id(20, "meta", "spend_no_purchase", CAMPAIGN)
    plan = reconcile(ev.findings, {}, ev.evaluated_rules, set(), {}, 2, "meta")
    assert plan.changes == [(fid, "opened")]

    existing = {fid: {"state": "open", "severity": "warning", "detector": "live.meta.spend_no_purchase"}}
    plan = reconcile(ev.findings, existing, ev.evaluated_rules, set(), {}, 2, "meta")
    assert plan.changes == [(fid, "escalated")]

    gone = reconcile([], existing, {"spend_no_purchase"}, set(), {}, 2, "meta")
    assert gone.resolves == [] and gone.misses == {fid: 1}
    gone = reconcile([], existing, {"spend_no_purchase"}, set(), {fid: 2}, 2, "meta")
    assert gone.resolves == [fid] and gone.changes == [(fid, "resolved")]
    not_run = reconcile([], existing, {"roas_below"}, set(), {fid: 5}, 2, "meta")
    assert not_run.resolves == []
    frozen = reconcile([], existing, {"spend_no_purchase"}, {"spend_no_purchase"}, {fid: 5}, 2, "meta")
    assert frozen.resolves == []


def test_missing_window_freezes_performance_rules():
    snap = world()
    del snap.account().metrics["3d"]
    frozen = frozen_rules(snap)
    assert "roas_below" in frozen and "missing_campaign_label" not in frozen
    assert frozen_rules(world()) == set()


def test_entities_not_delivering_are_skipped_except_status_rule():
    snap = world(adset={"effective_status": "CAMPAIGN_PAUSED"}, metrics={"adset": {"today": m(spend=4000)}})
    config = _only(cfg(), "spend_no_purchase")
    ev = evaluate(ctx(snap, config=config), config, config.tenants[0], {"5m"})
    assert ADSET not in {f.entity.id for f in ev.findings}


def test_campaign_with_every_ad_paused_is_not_judged():
    # ACTIVE campaign and ad set, ad paused at 17:55: nothing can spend, so no pacing or stall finding
    snap = world(ad={"status": "PAUSED", "effective_status": "PAUSED"},
                 metrics={"campaign": {"today": m(spend=50)}, "account": {"today": m(spend=50)}})
    assert not snap.deliverable(snap.get(CAMPAIGN)) and snap.deliverable(snap.account())
    ev = evaluate(ctx(snap), cfg(), cfg().tenants[0])
    assert not [f for f in ev.findings if f.rule_id == "spend_pacing"]
    assert not [f for f in ev.findings if f.entity.id in (CAMPAIGN, ADSET)]


def test_live_campaign_still_paced():
    snap = world(metrics={"campaign": {"today": m(spend=50)}, "account": {"today": m(spend=50)}})
    assert snap.deliverable(snap.get(CAMPAIGN))
    ev = evaluate(ctx(snap), cfg(), cfg().tenants[0])
    assert {f.entity.id for f in ev.findings if f.rule_id == "spend_pacing"} >= {CAMPAIGN}


def test_campaign_launched_today_paced_from_go_live():
    spend = {"campaign": {"today": m(spend=30)}, "account": {"today": m(spend=30)}}
    old = world(metrics=spend)
    assert [f for f in evaluate(ctx(old), cfg(), cfg().tenants[0]).findings if f.rule_id == "spend_pacing" and f.entity.id == CAMPAIGN]
    fresh = world(campaign={"created_at": NOW - timedelta(hours=1)}, metrics=spend)
    assert not [f for f in evaluate(ctx(fresh), cfg(), cfg().tenants[0]).findings if f.rule_id == "spend_pacing" and f.entity.id == CAMPAIGN]


def test_roas_supersedes_cpa_on_the_same_entity():
    # 3 and 7 days: ₹6,000 spend, 1 purchase worth ₹2,600 -> ROAS 0.43 (below break-even) and CPA ₹6,000 (above allowable)
    bad = m(spend=6000, purchases=1, purchase_value=2600, impressions=20000, link_clicks=300)
    snap = world(metrics={lvl: {"3d": bad, "7d": bad} for lvl in ("campaign", "adset", "ad")})
    found = [f for f in evaluate(ctx(snap), cfg(), cfg().tenants[0]).findings if f.rule_id in ("roas_below", "cpa_above_allowable")]
    assert len(found) == 1 and found[0].rule_id == "roas_below"
    assert found[0].hit.evidence["also"][0]["rule"] == "cpa_above_allowable"

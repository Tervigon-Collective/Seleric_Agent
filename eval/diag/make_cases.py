"""Write the committed eval cases (plan P0.4, P0.5) from the fixture, so segment names come from the data.

usage: python3 make_cases.py <fixture_id>

Names of campaigns/channels are read from the fixture, never typed. The null day is chosen by a rule that does not
involve the engine: the recent day whose net-sales value is closest to the median of the same weekday over the four
weeks before it. Truth is planted (injections) or independently recorded (outage); see each case's ``note``.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import world_fixture as W

HERE = Path(__file__).resolve().parent
OUTCOME = "net_sales"
SALES_CHAIN = [("orders", "units_ordered"), ("units_ordered", "gross_sales")]
FUNNEL_CHAIN = [("impressions", "link_clicks"), ("link_clicks", "sessions"), ("sessions", "orders")]


def chain(pairs: list[tuple[str, str]]) -> list[dict]:
    return [{"from": a, "to": b, "elasticity": 1.0} for a, b in pairs]


def top(fx: W.Fixture, metric: str, dim: str) -> str:
    return max(fx.segments[metric][dim].items(), key=lambda kv: sum(kv[1].values()))[0]


def null_day(fx: W.Fixture) -> date:
    s = fx.series[OUTCOME]
    best: tuple[float, date] | None = None
    for d in (fx.end - timedelta(days=i) for i in range(3, 24)):
        ref = [s[d - timedelta(days=7 * k)] for k in range(1, 5) if d - timedelta(days=7 * k) in s]
        if len(ref) == 4 and s.get(d):
            gap = abs(math.log(s[d] / statistics.median(ref)))
            if best is None or gap < best[0]:
                best = (gap, d)
    assert best is not None
    return best[1]


def case(cid: str, kind: str, tags: list[str], question: str, request: dict, truth: dict, fx: W.Fixture, injections: list[dict] | None = None, record: str = "") -> dict:
    out = {"schema_version": 1, "id": cid, "kind": kind, "brand": fx.brand, "tags": tags, "question": question, "request": request,
           "world": {"fixture": fx.id, "sha256": fx.sha256}, "truth": truth}
    if injections:
        out["injections"] = injections
    if record:
        out["record"] = record
    return out


def build(fx: W.Fixture) -> list[dict]:
    end = fx.end
    three = [str(end - timedelta(days=i)) for i in (2, 1, 0)]
    onset3 = str(end - timedelta(days=2))
    camp, chan = top(fx, "ad_spend", "campaign_name"), top(fx, "orders", "channel")
    pay = top(fx, "orders", "payment_bucket")
    q_drop = "Why did net sales drop?"
    ev = {"outcome": OUTCOME, "event_days": three, "claimed_direction": "down"}
    found = lambda cause, **kw: {"cause_class": "findable", "expected": "cause_found", "causes": [cause], **kw}
    cases = [
        case("inj_spend_cut_campaign", "injection", ["event", "findable", "ads", "scope_segment"], q_drop, ev,
             found({"metric": "ad_spend", "sign": "down", "onset": onset3, "share_of_change": 1.0}, unexplained_share=[0.0, 0.5],
                   note="ad spend of one campaign cut to 30% from the onset; effect carried through the funnel to net sales"),
             fx, [{"type": "scale", "onset": onset3, "params": {"metric": "ad_spend", "factor": 0.3, "scope": {"campaign_name": camp},
                                                                "effects": chain([("ad_spend", "impressions"), *FUNNEL_CHAIN, *SALES_CHAIN])}}]),
        case("inj_ctr_collapse_campaign", "injection", ["event", "findable", "funnel", "scope_segment"], q_drop, ev,
             found({"metric": "link_clicks", "sign": "down", "onset": onset3, "share_of_change": 1.0}, unexplained_share=[0.0, 0.5],
                   note="clicks of one campaign halve with impressions unchanged (a creative problem); carried to net sales"),
             fx, [{"type": "scale", "onset": onset3, "params": {"metric": "link_clicks", "factor": 0.5, "scope": {"campaign_name": camp},
                                                                "effects": chain([("link_clicks", "sessions"), ("sessions", "orders"), *SALES_CHAIN])}}]),
        case("inj_conversion_drop_channel", "injection", ["event", "findable", "conversion", "scope_segment"], q_drop, ev,
             found({"metric": "orders", "sign": "down", "onset": onset3, "share_of_change": 1.0}, unexplained_share=[0.0, 0.5],
                   note="orders of one channel fall 40% with traffic unchanged; carried to net sales"),
             fx, [{"type": "scale", "onset": onset3, "params": {"metric": "orders", "factor": 0.6, "scope": {"channel": chan},
                                                                "effects": chain(SALES_CHAIN)}}]),
        case("inj_slow_drift_sessions", "injection", ["drift", "findable", "traffic"], q_drop, {**ev, "mode": "drift"},
             found({"metric": "sessions", "sign": "down", "onset": str(end - timedelta(days=16)), "share_of_change": 1.0}, unexplained_share=[0.0, 0.6],
                   note="sessions slide to 60% over 14 days from 16 days before the end"),
             fx, [{"type": "scale", "onset": str(end - timedelta(days=16)),
                   "params": {"metric": "sessions", "factor": 0.6, "ramp_days": 14, "effects": chain([("sessions", "orders"), *SALES_CHAIN])}}]),
        case("inj_period_spend_cut", "injection", ["period", "findable", "ads"], "Why were net sales lower this week than last week?",
             {"outcome": OUTCOME, "event_days": [str(end - timedelta(days=i)) for i in range(6, -1, -1)],
              "baseline_days": [str(end - timedelta(days=i)) for i in range(13, 6, -1)], "claimed_direction": "down"},
             found({"metric": "ad_spend", "sign": "down", "onset": str(end - timedelta(days=6)), "share_of_change": 1.0}, unexplained_share=[0.0, 0.6],
                   note="account ad spend cut to 70% for the whole second week"),
             fx, [{"type": "scale", "onset": str(end - timedelta(days=6)),
                   "params": {"metric": "ad_spend", "factor": 0.7, "effects": chain([("ad_spend", "impressions"), *FUNNEL_CHAIN, *SALES_CHAIN])}}]),
        case("inj_filtered_scope_orders", "injection", ["event", "findable", "scope_filter"], "Why did net sales drop for one channel?",
             {**ev, "scope": {"channel": chan}},
             found({"metric": "orders", "sign": "down", "onset": onset3, "share_of_change": 1.0}, unexplained_share=[0.0, 0.5],
                   note="the question is filtered to the channel where orders were cut 40%"),
             fx, [{"type": "scale", "onset": onset3, "params": {"metric": "orders", "factor": 0.6, "scope": {"channel": chan},
                                                                "effects": chain(SALES_CHAIN)}}]),
        case("inj_off_graph_searches", "injection", ["event", "findable", "off_graph"], q_drop, ev,
             found({"metric": "site_searches", "sign": "down", "onset": onset3, "share_of_change": 1.0}, unexplained_share=[0.0, 0.6],
                   note="site searches fall 60%; the effect on orders is planted on a path the reviewed graph does not contain"),
             fx, [{"type": "scale", "onset": onset3, "params": {"metric": "site_searches", "factor": 0.4,
                                                                "effects": [{"from": "site_searches", "to": "orders", "elasticity": 0.5, "off_graph": True},
                                                                            *chain(SALES_CHAIN)]}}]),
        case("adv_break_funnel_purchases", "adversarial", ["event", "measurement", "tracking"], "Why did funnel purchases drop?",
             {"outcome": "funnel_purchases", "event_days": three, "claimed_direction": "down"},
             {"cause_class": "measurement", "expected": "measurement_artifact", "must_not_attribute": ["ad_spend", "sessions", "add_to_carts", "impressions"],
              "note": "the purchase event stops being recorded (x0.3); orders, the same quantity measured elsewhere, do not move"},
             fx, [{"type": "measurement_break", "onset": onset3, "params": {"metric": "funnel_purchases", "factor": 0.3}}]),
        case("adv_break_sessions", "adversarial", ["event", "measurement", "tracking"], "Why did sessions drop?",
             {"outcome": "sessions", "event_days": three, "claimed_direction": "down"},
             {"cause_class": "measurement", "expected": "measurement_artifact", "must_not_attribute": ["ad_spend", "impressions", "link_clicks"],
              "note": "session recording loses 60%; clicks and impressions do not move"},
             fx, [{"type": "measurement_break", "onset": onset3, "params": {"metric": "sessions", "factor": 0.4}}]),
        case("adv_hidden_orders_cut", "adversarial", ["event", "hidden"], q_drop, ev,
             {"cause_class": "hidden", "expected": "located_not_identified", "must_not_attribute": ["ad_spend", "sessions", "impressions", "discounts"],
              "note": "orders fall 50% with traffic and spend unchanged: a real cause with no visible driver (stock-out, price)"},
             fx, [{"type": "scale", "onset": onset3, "params": {"metric": "orders", "factor": 0.5, "effects": chain(SALES_CHAIN)}}]),
        case("adv_hidden_payment_outage", "adversarial", ["event", "hidden", "scope_segment"], q_drop, ev,
             {"cause_class": "hidden", "expected": "located_not_identified", "must_not_attribute": ["ad_spend", "sessions", "impressions"],
              "note": f"orders paid by one payment bucket ({pay}) fall 50%: a gateway-type cause that no metric shows"},
             fx, [{"type": "scale", "onset": onset3, "params": {"metric": "orders", "factor": 0.5, "scope": {"payment_bucket": pay},
                                                                "effects": chain(SALES_CHAIN)}}]),
        case("adv_premise_false_increase", "adversarial", ["event", "premise"], q_drop, ev,
             {"cause_class": "none", "expected": "premise_false", "must_not_attribute": ["ad_spend", "sessions"],
              "note": "net sales rose 50% (orders x1.5) while the question says they dropped"},
             fx, [{"type": "scale", "onset": onset3, "params": {"metric": "orders", "factor": 1.5, "effects": chain(SALES_CHAIN)}}]),
        case("null_ordinary_day", "null", ["event", "null"], q_drop, {"outcome": OUTCOME, "event_days": [str(null_day(fx))], "claimed_direction": "down"},
             {"cause_class": "none", "expected": "no_change", "must_not_attribute": ["ad_spend", "sessions", "orders"],
              "note": "the recent day whose net sales is closest to its same-weekday median of the previous four weeks; nothing is planted"},
             fx),
        case("rec_ad_outage_2026_09", "record", ["event", "findable", "record", "ads"], q_drop,
             {"outcome": OUTCOME, "event_days": ["2026-09-15", "2026-09-16", "2026-09-17"], "claimed_direction": "down"},
             found({"metric": "ad_spend", "sign": "down", "onset": "2026-09-15"}, unexplained_share=[0.0, 0.6],
                   note="paid spend is zero from 2026-09-15 to 2026-09-19 (ClickHouse serve.fct_paid_media_daily, brand 20: no spend rows, impressions 0)"),
             fx, record="ClickHouse serve.fct_paid_media_daily brand_id=20: spend 0 and impressions 0 on 2026-09-15..19, no rows 2026-09-20..22; memory 2026-09-14..22 ad outage"),
        case("stab_net_sales_2026_10_02", "stability", ["event", "stability"], q_drop,
             {"outcome": OUTCOME, "event_days": ["2026-10-02"], "claimed_direction": "down"},
             {"cause_class": "hidden", "expected": "located_not_identified",
              "note": "net sales fell 56% against neighbouring days; the cause is unknown. Scored for stability only, never for accuracy"},
             fx),
    ]
    return cases


def main(fixture_id: str) -> None:
    fx = W.load_fixture(fixture_id)
    out = HERE / "cases"
    out.mkdir(exist_ok=True)
    for c in build(fx):
        (out / f"{c['id']}.json").write_text(json.dumps(c, indent=1) + "\n")
        print(c["id"])


if __name__ == "__main__":
    main(sys.argv[1])

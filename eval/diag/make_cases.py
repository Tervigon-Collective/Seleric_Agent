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

import case_format as C
import inject
import runner as R
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


GRAPH = json.loads(R.GRAPH_PATH.read_text())
CALIBRATION_TOLERANCE = 0.02  # planted outcome change must land within 2 points of its target


def planted_change(fx: W.Fixture, injection: dict, outcome: str, days: list[str], scope: dict[str, str] | None = None) -> float:
    """Relative change of the outcome over ``days`` that the injection plants (world vs fixture); the engine is not involved."""
    parsed = C.parse_case(case("x", "injection", [], "q", {"outcome": outcome, "event_days": days}, {"cause_class": "findable", "expected": "cause_found",
                                                                                                     "causes": [{"metric": "m"}]}, fx, [injection]))
    world = inject.apply_injections(fx, parsed.injections, GRAPH)
    ds = [date.fromisoformat(d) for d in days]

    def total(series: dict, segs: dict) -> float:
        if scope:
            (dim, val), = scope.items()
            return sum(segs.get(outcome, {}).get(dim, {}).get(val, {}).get(d, 0.0) for d in ds)
        return sum(series[outcome].get(d, 0.0) for d in ds)

    before, after = total(fx.series, fx.segments), total(world.series, world.segments)
    return after / before - 1.0


def calibrate(fx: W.Fixture, make, outcome: str, days: list[str], target: float, scope: dict[str, str] | None = None,
              lo: float = 0.0, hi: float = 1.0) -> float:
    """The factor whose planted outcome change equals ``target`` (bisection; the change is monotone in the factor)."""
    f_lo, f_hi = lo, hi
    for _ in range(40):
        mid = (f_lo + f_hi) / 2
        change = planted_change(fx, make(mid), outcome, days, scope)
        if change < target:  # the change grows with the factor (toward 0 below 1, upward above 1)
            f_lo = mid
        else:
            f_hi = mid
    f = round((f_lo + f_hi) / 2, 3)
    got = planted_change(fx, make(f), outcome, days, scope)
    if abs(got - target) > CALIBRATION_TOLERANCE:
        raise ValueError(f"cannot plant {target:+.0%} on {outcome} with this scope: best factor {f} gives {got:+.1%}")
    return f


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
    week = [str(end - timedelta(days=i)) for i in range(6, -1, -1)]
    onset3 = str(end - timedelta(days=2))
    chan = top(fx, "orders", "channel")
    q_drop = "Why did net sales drop?"
    ev = {"outcome": OUTCOME, "event_days": three, "claimed_direction": "down"}

    def inj(onset: str, metric: str, factor: float, effects: list[dict], scope: dict | None = None, **extra) -> dict:
        params = {"metric": metric, "factor": factor, "effects": effects, **extra}
        if scope:
            params["scope"] = scope
        return {"type": "scale", "onset": onset, "params": params}

    def planted(make, target: float, days: list[str], outcome: str = OUTCOME, scope: dict | None = None, lo: float = 0.0, hi: float = 1.0):
        f = calibrate(fx, make, outcome, days, target, scope, lo, hi)
        return make(f), f, planted_change(fx, make(f), outcome, days, scope)

    def found(cause: dict, note: str, ratio: float) -> dict:
        return {"cause_class": "findable", "expected": "cause_found", "causes": [cause], "unexplained_share": [0.0, 0.5],
                "note": f"{note}; planted change of the outcome {ratio:+.0%} over the event days"}

    cases: list[dict] = []

    def add(cid, kind, tags, question, request, truth, injections=None, record=""):
        cases.append(case(cid, kind, tags, question, request, truth, fx, injections, record))

    # -- findable worlds: every planted effect is sized to move the outcome materially (target in each line) --------
    m, f, r = planted(lambda x: inj(onset3, "ad_spend", x, chain([("ad_spend", "impressions"), *FUNNEL_CHAIN, *SALES_CHAIN]), {"ad_platform": "meta"}), -0.30, three)
    add("inj_spend_cut_platform", "injection", ["event", "findable", "ads", "scope_segment"], q_drop, ev,
        found({"metric": "ad_spend", "sign": "down", "onset": onset3, "share_of_change": 1.0}, f"ad spend on one platform cut to {f:.0%}, carried through the funnel to net sales", r), [m])
    m, f, r = planted(lambda x: inj(onset3, "link_clicks", x, chain([("link_clicks", "sessions"), ("sessions", "orders"), *SALES_CHAIN]), {"ad_platform": "meta"}), -0.25, three)
    add("inj_ctr_collapse_platform", "injection", ["event", "findable", "funnel", "scope_segment"], q_drop, ev,
        found({"metric": "link_clicks", "sign": "down", "onset": onset3, "share_of_change": 1.0}, f"clicks on one platform cut to {f:.0%} with impressions unchanged (a creative problem)", r), [m])
    m, f, r = planted(lambda x: inj(onset3, "orders", x, chain(SALES_CHAIN), {"channel": chan}), -0.20, three)
    add("inj_conversion_drop_channel", "injection", ["event", "findable", "conversion", "scope_segment"], q_drop, ev,
        found({"metric": "orders", "sign": "down", "onset": onset3, "share_of_change": 1.0}, f"orders of one channel cut to {f:.0%} with traffic unchanged", r), [m])
    drift_on = str(end - timedelta(days=16))
    m, f, r = planted(lambda x: inj(drift_on, "sessions", x, chain([("sessions", "orders"), *SALES_CHAIN]), None, ramp_days=14), -0.30, three)
    add("inj_slow_drift_sessions", "injection", ["drift", "findable", "traffic"], q_drop, {**ev, "mode": "drift"},
        found({"metric": "sessions", "sign": "down", "onset": drift_on, "share_of_change": 1.0}, f"sessions slide to {f:.0%} over 14 days starting 16 days before the end", r), [m])
    m, f, r = planted(lambda x: inj(week[0], "ad_spend", x, chain([("ad_spend", "impressions"), *FUNNEL_CHAIN, *SALES_CHAIN])), -0.25, week)
    add("inj_period_spend_cut", "injection", ["period", "findable", "ads"], "Why were net sales lower this week than last week?",
        {"outcome": OUTCOME, "event_days": week, "baseline_days": [str(end - timedelta(days=i)) for i in range(13, 6, -1)], "claimed_direction": "down"},
        found({"metric": "ad_spend", "sign": "down", "onset": week[0], "share_of_change": 1.0}, f"account ad spend cut to {f:.0%} for the whole second week", r), [m])
    m, f, r = planted(lambda x: inj(onset3, "orders", x, chain(SALES_CHAIN), {"channel": chan}), -0.50, three, OUTCOME, {"channel": chan})
    add("inj_filtered_scope_orders", "injection", ["event", "findable", "scope_filter"], "Why did net sales drop for one channel?", {**ev, "scope": {"channel": chan}},
        found({"metric": "orders", "sign": "down", "onset": onset3, "share_of_change": 1.0}, f"the question is filtered to the channel whose orders were cut to {f:.0%}", r), [m])
    m, f, r = planted(lambda x: inj(onset3, "site_searches", x, [{"from": "site_searches", "to": "orders", "elasticity": 0.5, "off_graph": True}, *chain(SALES_CHAIN)]), -0.25, three, lo=0.0, hi=1.0)
    add("inj_off_graph_searches", "injection", ["event", "findable", "off_graph"], q_drop, ev,
        found({"metric": "site_searches", "sign": "down", "onset": onset3, "share_of_change": 1.0}, f"site searches cut to {f:.0%}; the effect on orders is planted on a path the reviewed graph does not contain", r), [m])

    # -- adversarial worlds: the engine must not name a cause that is not there --------------------------------------
    add("adv_break_funnel_purchases", "adversarial", ["event", "measurement", "tracking"], "Why did funnel purchases drop?",
        {"outcome": "funnel_purchases", "event_days": three, "claimed_direction": "down"},
        {"cause_class": "measurement", "expected": "measurement_artifact", "must_not_attribute": ["ad_spend", "sessions", "add_to_carts", "impressions"],
         "note": "the purchase event stops being recorded (x0.3); orders, the same quantity measured elsewhere, do not move"},
        [{"type": "measurement_break", "onset": onset3, "params": {"metric": "funnel_purchases", "factor": 0.3}}])
    add("adv_break_sessions", "adversarial", ["event", "measurement", "tracking"], "Why did sessions drop?",
        {"outcome": "sessions", "event_days": three, "claimed_direction": "down"},
        {"cause_class": "measurement", "expected": "measurement_artifact", "must_not_attribute": ["ad_spend", "impressions", "link_clicks"],
         "note": "session recording loses 60%; clicks and impressions do not move"},
        [{"type": "measurement_break", "onset": onset3, "params": {"metric": "sessions", "factor": 0.4}}])
    m, f, r = planted(lambda x: inj(onset3, "orders", x, chain(SALES_CHAIN)), -0.30, three)
    add("adv_hidden_orders_cut", "adversarial", ["event", "hidden"], q_drop, ev,
        {"cause_class": "hidden", "expected": "located_not_identified", "must_not_attribute": ["ad_spend", "sessions", "impressions", "discounts"],
         "note": f"orders cut to {f:.0%} with traffic and spend unchanged: a real cause with no visible driver (stock-out, price); outcome {r:+.0%}"}, [m])
    m, f, r = planted(lambda x: inj(onset3, "orders", x, chain(SALES_CHAIN), {"channel": chan}), -0.20, three)
    add("adv_hidden_channel_checkout", "adversarial", ["event", "hidden", "scope_segment"], q_drop, ev,
        {"cause_class": "hidden", "expected": "located_not_identified", "must_not_attribute": ["ad_spend", "sessions", "impressions"],
         "note": f"orders of one channel cut to {f:.0%}: a checkout-type cause that no metric shows; outcome {r:+.0%}"}, [m])
    m, f, r = planted(lambda x: inj(onset3, "orders", x, chain(SALES_CHAIN)), +0.30, three, lo=1.0, hi=3.0)
    add("adv_premise_false_increase", "adversarial", ["event", "premise"], q_drop, ev,
        {"cause_class": "none", "expected": "premise_false", "must_not_attribute": ["ad_spend", "sessions"],
         "note": f"net sales rose {r:+.0%} (orders x{f}) while the question says they dropped"}, [m])
    add("null_ordinary_day", "null", ["event", "null"], q_drop, {"outcome": OUTCOME, "event_days": [str(null_day(fx))], "claimed_direction": "down"},
        {"cause_class": "none", "expected": "no_change", "must_not_attribute": ["ad_spend", "sessions", "orders"],
         "note": "the recent day whose net sales is closest to its same-weekday median of the previous four weeks; nothing is planted"})

    # -- independent records and unknown incidents -------------------------------------------------------------------
    add("rec_ad_outage_2026_09", "record", ["event", "findable", "record", "ads"], q_drop,
        {"outcome": OUTCOME, "event_days": ["2026-09-15", "2026-09-16", "2026-09-17"], "claimed_direction": "down"},
        {"cause_class": "findable", "expected": "cause_found", "causes": [{"metric": "ad_spend", "sign": "down", "onset": "2026-09-15"}], "unexplained_share": [0.0, 0.6],
         "note": "paid spend is zero from 2026-09-15 to 2026-09-19 (ClickHouse serve.fct_paid_media_daily, brand 20: no spend rows, impressions 0)"},
        record="ClickHouse serve.fct_paid_media_daily brand_id=20: spend 0 and impressions 0 on 2026-09-15..19, no rows 2026-09-20..22; memory 2026-09-14..22 ad outage")
    add("stab_net_sales_2026_10_02", "stability", ["event", "stability"], q_drop, {"outcome": OUTCOME, "event_days": ["2026-10-02"], "claimed_direction": "down"},
        {"cause_class": "hidden", "expected": "located_not_identified",
         "note": "net sales fell 56% against neighbouring days; the cause is unknown. Scored for stability only, never for accuracy"})
    return cases


def main(fixture_id: str) -> None:
    import case_format as C
    import runner as R

    fx = W.load_fixture(fixture_id)
    out = HERE / "cases"
    out.mkdir(exist_ok=True)
    for c in build(fx):
        parsed = C.parse_case(c)
        R.check_reaches_outcome(parsed, R.build_world(parsed)[1])  # an unbuildable or unconnected case fails here, not mid-suite
        (out / f"{c['id']}.json").write_text(json.dumps(c, indent=1) + "\n")
        print(c["id"])


if __name__ == "__main__":
    main(sys.argv[1])

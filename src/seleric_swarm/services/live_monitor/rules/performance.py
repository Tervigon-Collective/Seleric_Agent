"""P0 performance rules. Every threshold is a param (editable in Seleric Agent); defaults come from brand 20 history.

Money is in rupees. Ratio judgments at entity level go through ``core.stats`` (probability beyond the line at least
``stats.confidence``), never a raw comparison, because a median ad has about 2 purchases in 14 days.
"""

from __future__ import annotations

from pydantic import Field

from seleric_swarm.services.live_monitor.core import stats as S
from seleric_swarm.services.live_monitor.core.context import RuleContext
from seleric_swarm.services.live_monitor.core.model import Entity, Hit
from seleric_swarm.services.live_monitor.core.registry import Params, register_rule

PLAYBOOK = "Meta Ads Playbook §7 Monitoring"


def _m(e: Entity, window: str, key: str) -> float:
    v = e.metric(window, key)
    return 0.0 if v is None else float(v)


def _inr(x: float | None) -> str:
    return "n/a" if x is None else f"₹{x:,.0f}"


def daily_budget_inr(ctx: RuleContext, e: Entity) -> float | None:
    """The day's budget that governs ``e``: its own daily budget, else the sum of its children's (ABO)."""
    own = e.attrs.get("daily_budget")
    if own:
        return float(own) / 100.0
    if e.level == "account":
        vals = [daily_budget_inr(ctx, c) for c in ctx.snapshot.at("campaign") if ctx.snapshot.deliverable(c)]
    elif e.level == "campaign":
        vals = [float(s.attrs["daily_budget"]) / 100.0 for s in ctx.snapshot.at("adset")
                if s.parent_id == e.id and ctx.snapshot.deliverable(s) and s.attrs.get("daily_budget")]
    else:
        return None
    vals = [v for v in vals if v]
    return sum(vals) if vals else None


# ------------------------------------------------------------------------------------------------ 1 pacing

class PacingParams(Params):
    over_warning: float = Field(1.5, ge=1.05, le=3, description="Spend at least this multiple of expected-by-now")
    over_critical: float = Field(1.75, ge=1.1, le=5, description="Meta may spend up to 1.75x the daily budget in a day")
    under_warning: float = Field(0.4, ge=0.05, le=0.95, description="Spend below this share of expected")
    under_after_hour: int = Field(12, ge=6, le=22, description="Judge under-pacing only from this local hour")
    min_expected_inr: float = Field(500, ge=0, le=100000)


@register_rule(
    id="spend_pacing", title="Spend pacing", tier="performance", levels=("account", "campaign"), cadence="5m",
    params=PacingParams, actions=("pause", "open_agent"), playbook_ref=PLAYBOOK,
    description="Today's spend against budget times the share of a normal day's spend done by this hour.",
)
def spend_pacing(ctx: RuleContext, e: Entity, p: PacingParams) -> Hit | None:
    budget = daily_budget_inr(ctx, e)
    if not budget:
        return None
    now = ctx.now_local

    def share_at(t) -> float:
        s = ctx.baselines.cum_share("spend", t.hour, t.minute)
        return (t.hour + t.minute / 60.0) / 24.0 if s is None else s

    share = share_at(now)
    born = e.created_at.astimezone(now.tzinfo) if e.created_at else None
    if born and born.date() == now.date():  # launched today: only the part of the day since it went live
        share = max(0.0, share - share_at(born))
    expected = budget * share
    if expected < p.min_expected_inr:
        return None
    spend = _m(e, "today", "spend")
    ratio = spend / expected
    ev = {"spend_today": spend, "expected_by_now": round(expected, 0), "daily_budget": budget, "pace": round(ratio, 2),
          "expected_share": round(share, 3), "min_expected_inr": p.min_expected_inr,
          "curve": "hour-of-day history" if ctx.baselines.curves.get("spend") else "linear"}
    if ratio >= p.over_warning:
        sev = "critical" if ratio >= p.over_critical else "warning"
        return Hit(sev, f"Spending fast: {_inr(spend)} by {now:%H:%M} against {_inr(expected)} expected ({ratio:.0%} of pace).",
                   ev, impact_inr=spend - expected, confidence=None,
                   actions=("pause", "open_agent") if sev == "critical" and e.level != "account" else ("open_agent",))
    if now.hour >= p.under_after_hour and ratio <= p.under_warning:
        return Hit("warning", f"Under-delivering: {_inr(spend)} by {now:%H:%M} against {_inr(expected)} expected ({ratio:.0%} of pace).",
                   ev, impact_inr=expected - spend, confidence=None, actions=("open_agent",))
    return None


# ------------------------------------------------------------------------------------- 2 spend no purchase

class NoPurchaseParams(Params):
    warn_multiple: float = Field(1.5, ge=0.5, le=10, description="Spend today >= this x allowable CPA with 0 purchases")
    critical_multiple: float = Field(3.0, ge=1, le=20)
    max_p_zero: float = Field(0.1, gt=0, le=0.5, description="Fire only when 0 purchases is this unlikely at the usual rate")


@register_rule(
    id="spend_no_purchase", title="Spend with no purchase", tier="performance", levels=("campaign", "adset", "ad"),
    cadence="5m", params=NoPurchaseParams, actions=("pause", "open_agent"), playbook_ref=PLAYBOOK, efficiency=True, rollup=True,
    description="Spent a multiple of the allowable CPA today without a purchase, which is unlikely at its usual rate.",
)
def spend_no_purchase(ctx: RuleContext, e: Entity, p: NoPurchaseParams) -> Hit | None:
    spend, purchases = _m(e, "today", "spend"), _m(e, "today", "purchases")
    allowable = ctx.guard(e).allowable_cpa
    if purchases > 0 or spend < p.warn_multiple * allowable:
        return None
    rate = ctx.prior_rate(e)
    p0 = S.prob_zero(rate, spend)
    if p0 >= p.max_p_zero:
        return None
    sev = "critical" if spend >= p.critical_multiple * allowable else "warning"
    return Hit(sev, f"{_inr(spend)} spent today with no purchase ({spend / allowable:.1f}x the allowable CPA {_inr(allowable)}).",
               {"spend_today": spend, "purchases_today": 0, "allowable_cpa": allowable, "usual_cpa": round(1 / rate, 0),
                "p_zero_at_usual_rate": round(p0, 3), "min_spend_ok": True, "confidence": round(1 - p0, 3)},
               impact_inr=spend, confidence=round(1 - p0, 3), actions=("pause", "open_agent"))


# ---------------------------------------------------------------------------------------------- 3 CPA

class CpaParams(Params):
    min_spend_multiple: float = Field(2.0, ge=0.5, le=20, description="3-day spend >= this x allowable CPA")


@register_rule(
    id="cpa_above_allowable", title="CPA above allowable", tier="performance", levels=("campaign", "adset"),
    cadence="1h", params=CpaParams, actions=("budget_down", "pause", "open_agent"), playbook_ref=PLAYBOOK,
    efficiency=True, rollup=True,
    description="3-day CPA is above the allowable CPA with the configured confidence; critical when 7 days agree.",
)
def cpa_above_allowable(ctx: RuleContext, e: Entity, p: CpaParams) -> Hit | None:
    allowable = ctx.guard(e).allowable_cpa
    spend, k = _m(e, "3d", "spend"), _m(e, "3d", "purchases")
    if spend < p.min_spend_multiple * allowable:
        return None
    rate, pe, conf = ctx.prior_rate(e), ctx.stats.prior_events, ctx.stats.confidence
    p3 = S.prob_cpa_above(allowable, k, spend, rate, pe)
    if p3 < conf:
        return None
    p7 = S.prob_cpa_above(allowable, _m(e, "7d", "purchases"), _m(e, "7d", "spend"), rate, pe)
    sev = "critical" if p7 >= conf else "warning"
    cpa = spend / k if k else None
    return Hit(sev, f"3-day CPA {_inr(cpa) if cpa else 'no purchases'} on {_inr(spend)} spend, above the allowable {_inr(allowable)}.",
               {"spend_3d": spend, "purchases_3d": k, "cpa_3d": cpa, "allowable_cpa": allowable, "p_above_3d": round(p3, 3),
                "p_above_7d": round(p7, 3), "min_spend_ok": True, "confidence": round(p3, 3)},
               impact_inr=max(spend - k * allowable, 0.0), confidence=round(p3, 3), actions=("budget_down", "pause", "open_agent"))


# ---------------------------------------------------------------------------------------------- 4 ROAS

class RoasParams(Params):
    min_spend_inr: float = Field(5000, ge=0, le=1_000_000)


@register_rule(
    id="roas_below", title="ROAS below target / break-even", tier="performance", levels=("campaign", "adset"),
    cadence="1h", params=RoasParams, actions=("budget_down", "pause", "open_agent"), playbook_ref=PLAYBOOK,
    efficiency=True, rollup=True, supersedes=("cpa_above_allowable",),
    description="Warning: 3-day ROAS likely below the target. Critical: likely below break-even on 3 and 7 days.",
)
def roas_below(ctx: RuleContext, e: Entity, p: RoasParams) -> Hit | None:
    spend = _m(e, "3d", "spend")
    if spend < p.min_spend_inr:
        return None
    g = ctx.guard(e)
    k, value = _m(e, "3d", "purchases"), _m(e, "3d", "purchase_value")
    rate, aov, st = ctx.prior_rate(e), ctx.prior_aov(e), ctx.stats
    below_be = S.prob_roas_below(g.be_roas_platform, k, spend, value, rate, aov, st.prior_events, st.prior_orders)
    below_target = S.prob_roas_below(g.target_roas, k, spend, value, rate, aov, st.prior_events, st.prior_orders)
    roas = value / spend if spend else 0.0
    ev = {"spend_3d": spend, "purchase_value_3d": value, "roas_3d": round(roas, 2), "target_roas": round(g.target_roas, 2),
          "be_roas": round(g.be_roas_platform, 2), "p_below_target": round(below_target, 3), "p_below_be": round(below_be, 3),
          "min_spend_ok": True, "min_spend_inr": p.min_spend_inr}
    if below_be >= st.confidence:
        s7, v7, k7 = _m(e, "7d", "spend"), _m(e, "7d", "purchase_value"), _m(e, "7d", "purchases")
        p7 = S.prob_roas_below(g.be_roas_platform, k7, s7, v7, rate, aov, st.prior_events, st.prior_orders)
        ev["p_below_be_7d"] = round(p7, 3)
        if p7 >= st.confidence:
            return Hit("critical", f"3-day ROAS {roas:.2f} is below break-even {g.be_roas_platform:.2f}, and the 7-day trend agrees.",
                       {**ev, "confidence": round(below_be, 3)},
                       impact_inr=max(g.be_roas_platform * spend - value, 0.0) / g.be_roas_platform,
                       confidence=round(below_be, 3), actions=("budget_down", "pause", "open_agent"))
    if below_target >= st.confidence:
        return Hit("warning", f"3-day ROAS {roas:.2f} is below the target {g.target_roas:.2f} (break-even {g.be_roas_platform:.2f}).",
                   {**ev, "confidence": round(below_target, 3)},
                   impact_inr=max(g.target_roas * spend - value, 0.0) / g.target_roas,
                   confidence=round(below_target, 3), actions=("budget_down", "open_agent"))
    return None


# ---------------------------------------------------------------------------------------------- 5 CPM

class CpmParams(Params):
    multiple_of_p90: float = Field(1.2, ge=1.0, le=5)
    min_impressions: float = Field(5000, ge=100, le=10_000_000)


@register_rule(
    id="cpm_spike", title="CPM spike", tier="performance", levels=("campaign", "adset"), cadence="1h", params=CpmParams,
    actions=("open_agent",), playbook_ref=PLAYBOOK, rollup=True,
    description="Today's CPM is above its usual high (p90) by the configured multiple.",
)
def cpm_spike(ctx: RuleContext, e: Entity, p: CpmParams) -> Hit | None:
    impressions = _m(e, "today", "impressions")
    cpm = e.metric("today", "cpm")
    band = ctx.band(e, "cpm")
    if impressions < p.min_impressions or cpm is None or band is None or band.p90 is None:
        return None
    if cpm <= p.multiple_of_p90 * band.p90:
        return None
    usual = band.p50 or band.p90
    return Hit("warning", f"CPM {_inr(cpm)} today against a usual {_inr(usual)} (high {_inr(band.p90)}).",
               {"cpm_today": round(cpm, 0), "cpm_p50": usual, "cpm_p90": band.p90, "impressions_today": impressions,
                "min_impressions_ok": True, "baseline": band.source},
               impact_inr=max(cpm - usual, 0.0) * impressions / 1000.0, actions=("open_agent",))


# ---------------------------------------------------------------------------------------------- 6 CTR

class CtrParams(Params):
    min_impressions: float = Field(5000, ge=100, le=10_000_000)


@register_rule(
    id="ctr_drop", title="CTR drop", tier="performance", levels=("ad",), cadence="1h", params=CtrParams,
    actions=("open_agent", "pause"), playbook_ref=PLAYBOOK,
    description="Today's link CTR is likely below the ad's usual low (p10).",
)
def ctr_drop(ctx: RuleContext, e: Entity, p: CtrParams) -> Hit | None:
    n, clicks = _m(e, "today", "impressions"), _m(e, "today", "link_clicks")
    band = ctx.band(e, "ctr_link")
    if n < p.min_impressions or band is None or band.p10 is None:
        return None
    prior = (band.p50 or band.p10) / 100.0
    prob = S.prob_p_below(band.p10 / 100.0, clicks, n, prior, ctx.stats.prior_trials_ctr)
    if prob < ctx.stats.confidence:
        return None
    ctr = clicks / n * 100.0
    return Hit("warning", f"Link CTR {ctr:.2f}% today, below this ad's usual low {band.p10:.2f}% (typical {prior * 100:.2f}%).",
               {"ctr_today": round(ctr, 3), "ctr_p10": band.p10, "ctr_p50": band.p50, "impressions_today": n,
                "p_below_p10": round(prob, 3), "min_impressions_ok": True, "confidence": round(prob, 3),
                "baseline": band.source},
               impact_inr=None, confidence=round(prob, 3), actions=("open_agent", "pause"))


# ----------------------------------------------------------------------------------------- 7 fatigue

class FatigueParams(Params):
    freq_warning: float = Field(2.0, ge=1.0, le=20)
    freq_critical: float = Field(2.5, ge=1.0, le=30)
    ctr_drop_pct: float = Field(20, ge=5, le=90, description="3-day CTR at least this % below the 7 days before")
    min_impressions: float = Field(3000, ge=100, le=10_000_000)


@register_rule(
    id="creative_fatigue", title="Creative fatigue", tier="performance", levels=("ad",), cadence="daily",
    params=FatigueParams, actions=("open_agent", "pause"), playbook_ref=PLAYBOOK,
    description="7-day frequency is high and the last 3 days' CTR fell against the 7 days before.",
)
def creative_fatigue(ctx: RuleContext, e: Entity, p: FatigueParams) -> Hit | None:
    freq = e.metric("7d", "frequency")
    ctr3, ctrp = e.metric("3d", "ctr_link"), e.metric("prev7d", "ctr_link")
    if freq is None or ctr3 is None or not ctrp or freq < p.freq_warning:
        return None
    if _m(e, "3d", "impressions") < p.min_impressions or _m(e, "prev7d", "impressions") < p.min_impressions:
        return None
    drop = 1.0 - ctr3 / ctrp
    if drop * 100.0 < p.ctr_drop_pct:
        return None
    sev = "critical" if freq >= p.freq_critical else "warning"
    return Hit(sev, f"Frequency {freq:.2f} over 7 days and CTR down {drop:.0%} ({ctrp:.2f}% to {ctr3:.2f}%): the creative is tiring.",
               {"frequency_7d": round(freq, 2), "ctr_3d": round(ctr3, 3), "ctr_prev7d": round(ctrp, 3), "ctr_drop": round(drop, 3),
                "impressions_3d": _m(e, "3d", "impressions"), "min_impressions_ok": True},
               impact_inr=None, actions=("open_agent", "pause"))


# ----------------------------------------------------------------------------------------- 8 landing

class LandingParams(Params):
    min_clicks: float = Field(200, ge=20, le=100_000)
    warning_below: float = Field(0.80, gt=0, lt=1)
    critical_below: float = Field(0.50, gt=0, lt=1)


def _landing_url(ctx: RuleContext, e: Entity) -> str | None:
    if e.attrs.get("link"):
        return e.attrs["link"]
    for ad in ctx.snapshot.at("ad"):
        if ad.campaign_id == e.id and ad.attrs.get("link"):
            return ad.attrs["link"]
    return None


@register_rule(
    id="landing_break", title="Landing page break", tier="performance", levels=("campaign", "ad"), cadence="5m",
    params=LandingParams, actions=("open_agent",), playbook_ref=PLAYBOOK, rollup=True,
    description="Landing-page views per link click fell: the page may be slow, broken or redirecting.",
)
def landing_break(ctx: RuleContext, e: Entity, p: LandingParams) -> Hit | None:
    clicks, lpv = _m(e, "today", "link_clicks"), _m(e, "today", "lpv")
    if clicks < p.min_clicks:
        return None
    ratio = lpv / clicks
    if ratio >= p.warning_below:
        return None
    sev = "critical" if ratio < p.critical_below else "warning"
    band = ctx.band(e, "lpv_per_click")
    return Hit(sev, f"Only {ratio:.0%} of {clicks:,.0f} link clicks reached the landing page today (usual {(band.p50 if band and band.p50 else 0.94):.0%}).",
               {"link_clicks_today": clicks, "lpv_today": lpv, "lpv_per_click": round(ratio, 3), "url": _landing_url(ctx, e),
                "min_clicks_ok": True, "spend_today": _m(e, "today", "spend")},
               impact_inr=_m(e, "today", "spend") * (1 - ratio), actions=("open_agent",))


# ------------------------------------------------------------------------------------- 9 conversion

class ConversionParams(Params):
    min_lpv: float = Field(300, ge=50, le=1_000_000)
    drop_to: float = Field(0.5, gt=0, lt=1, description="Purchase per LPV below this share of usual")
    critical_to: float = Field(0.25, gt=0, lt=1)


@register_rule(
    id="conversion_drop", title="Conversion drop", tier="performance", levels=("account", "campaign"), cadence="1h",
    params=ConversionParams, actions=("open_agent",), playbook_ref=PLAYBOOK, rollup=True,
    description="Purchases per landing-page view fell while CTR is normal: a checkout, stock or pixel problem, not the ad.",
)
def conversion_drop(ctx: RuleContext, e: Entity, p: ConversionParams) -> Hit | None:
    lpv, k = _m(e, "today", "lpv"), _m(e, "today", "purchases")
    band = ctx.band(e, "cvr_lpv")
    if lpv < p.min_lpv or band is None or not (band.mean or band.p50):
        return None
    usual = float(band.mean or band.p50) / 100.0
    ctr_band, ctr = ctx.band(e, "ctr_link"), e.metric("today", "ctr_link")
    if ctr_band and ctr_band.p10 and ctr is not None and ctr < ctr_band.p10:
        return None
    st = ctx.stats
    p_warn = S.prob_p_below(usual * p.drop_to, k, lpv, usual, st.prior_trials_cvr)
    if p_warn < st.confidence:
        return None
    p_crit = S.prob_p_below(usual * p.critical_to, k, lpv, usual, st.prior_trials_cvr)
    sev = "critical" if p_crit >= st.confidence else "warning"
    cvr = k / lpv
    return Hit(sev, f"{cvr:.2%} of landing-page visitors bought today against a usual {usual:.2%} while CTR is normal.",
               {"lpv_today": lpv, "purchases_today": k, "cvr_today": round(cvr * 100, 3), "cvr_usual": round(usual * 100, 3),
                "p_below_half": round(p_warn, 3), "min_lpv_ok": True, "confidence": round(p_warn, 3),
                "baseline": band.source},
               impact_inr=(usual * lpv - k) * ctx.prior_aov(e) / max(ctx.guard(e).be_roas_platform, 0.1),
               confidence=round(p_warn, 3), actions=("open_agent",))


# --------------------------------------------------------------------------------------- 10 tracking

class TrackingParams(Params):
    ratio_band: float = Field(0.4, gt=0.05, le=2, description="Pixel/store ratio more than this share off its usual value")
    min_store_orders: float = Field(10, ge=1, le=10_000)
    check_atc: bool = True


@register_rule(
    id="tracking_gap", title="Tracking gap", tier="performance", levels=("account",), cadence="daily", params=TrackingParams,
    actions=("open_agent",), playbook_ref="Meta Ads Playbook §5 Tracking",
    description="Pixel purchases moved away from the store's Meta-attributed orders, or add-to-cart fires less than purchase.",
)
def tracking_gap(ctx: RuleContext, e: Entity, p: TrackingParams) -> Hit | None:
    days = list((ctx.tracking or {}).get("days") or [])
    median = (ctx.tracking or {}).get("median_ratio")
    problems, ev, sev = [], {}, None
    if days and median:
        last = days[-1]
        store, pixel = float(last["store_orders"]), float(last["pixel_purchases"])
        if store >= p.min_store_orders:
            ratio = pixel / store
            ev.update(date=last["date"], pixel_purchases=pixel, store_orders=store, ratio=round(ratio, 2), usual_ratio=round(median, 2))
            if pixel == 0 or abs(ratio / median - 1) > p.ratio_band:
                sev = "critical" if pixel == 0 else "warning"
                problems.append(f"on {last['date']} the pixel counted {pixel:.0f} purchases for {store:.0f} store orders "
                                f"({ratio:.2f}x, usually {median:.2f}x)")
    recent = days[-7:]
    if p.check_atc and recent:
        atc, purchases = sum(d["pixel_atc"] for d in recent), sum(d["pixel_purchases"] for d in recent)
        ev.update(atc_7d=atc, purchases_7d=purchases)
        if purchases >= 10 and atc < purchases:
            sev = sev or "info"  # known chronic under-fire; keep visible, do not page
            problems.append(f"add-to-cart fired {atc:.0f} times against {purchases:.0f} purchases in 7 days (it is under-firing)")
    if not problems:
        return None
    return Hit(sev or "warning", "Tracking: " + "; ".join(problems) + ".", ev, actions=("open_agent",))


# ------------------------------------------------------------------------------------ 11 delivery stall

class StallParams(Params):
    start_hour: int = Field(8, ge=0, le=23)
    end_hour: int = Field(23, ge=1, le=23)
    min_expected_impressions: float = Field(200, ge=10, le=1_000_000)
    critical_expected_impressions: float = Field(1000, ge=10, le=10_000_000)
    min_age_hours: float = Field(2, ge=0, le=72)


@register_rule(
    id="delivery_stall", title="Delivery stall", tier="performance", levels=("campaign", "adset"), cadence="5m",
    params=StallParams, actions=("open_agent",), playbook_ref=PLAYBOOK, rollup=True,
    description="An ACTIVE entity delivered nothing in the last full hour and so far this hour, when history expects delivery.",
)
def delivery_stall(ctx: RuleContext, e: Entity, p: StallParams) -> Hit | None:
    now = ctx.now_local
    prev = now.hour - 1
    if prev < p.start_hour or now.hour > p.end_hour:
        return None
    if e.created_at is not None and (now - e.created_at.astimezone(now.tzinfo)).total_seconds() < p.min_age_hours * 3600:
        return None
    hours = ctx.snapshot.hourly.get(e.id) or {}
    if (hours.get(prev) or {}).get("impressions", 0) > 0 or (hours.get(now.hour) or {}).get("impressions", 0) > 0:
        return None
    budget = daily_budget_inr(ctx, e)
    if budget and _m(e, "today", "spend") >= 0.95 * budget:
        return None
    band = ctx.band(e, "impressions", weekday=True)
    if band is None or not band.p50 or band.source.startswith("default"):
        return None
    share = ctx.baselines.hour_share("impressions", prev) or (1.0 / 24.0)
    expected = band.p50 * share
    if expected < p.min_expected_impressions:
        return None
    sev = "critical" if expected >= p.critical_expected_impressions else "warning"
    return Hit(sev, f"No impressions since {prev:02d}:00 while about {expected:,.0f} were expected in that hour.",
               {"hour": prev, "expected_impressions": round(expected, 0), "impressions_today": _m(e, "today", "impressions"),
                "spend_today": _m(e, "today", "spend"), "daily_budget": budget},
               actions=("open_agent",))


# ------------------------------------------------------------------------------------------ 12 learning

class LearningParams(Params):
    stuck_days: float = Field(7, ge=2, le=60)


@register_rule(
    id="learning_limited", title="Learning limited or stuck", tier="performance", levels=("adset",), cadence="daily",
    params=LearningParams, actions=("open_agent",), playbook_ref="Meta Ads Playbook §3 Ad sets",
    description="Meta reports the ad set as learning-limited, or it is still learning after the configured days.",
)
def learning_limited(ctx: RuleContext, e: Entity, p: LearningParams) -> Hit | None:
    info = e.attrs.get("learning_stage_info") or {}
    status = str(info.get("status") or "").upper()
    ev = {"learning_status": status, "conversions": info.get("conversions")}
    if status in ("FAIL", "LEARNING_LIMITED"):
        return Hit("warning", "Learning limited: too few conversions for Meta to optimise. Consolidate ad sets or broaden the audience.",
                   ev, actions=("open_agent",))
    if status == "LEARNING" and e.created_at is not None:
        age = (ctx.now_local - e.created_at.astimezone(ctx.now_local.tzinfo)).total_seconds() / 86400
        if age >= p.stuck_days:
            ev["age_days"] = round(age, 1)
            return Hit("info", f"Still learning after {age:.0f} days.", ev, actions=("open_agent",))
    return None


# ------------------------------------------------------------------------------------- 13 scale candidate

class ScaleParams(Params):
    multiple_of_be: float = Field(1.3, ge=1.0, le=5)
    min_spend_inr: float = Field(3000, ge=0, le=1_000_000)
    budget_use: float = Field(0.9, ge=0.5, le=1.2, description="Average spend of the last 2 full days >= this share of budget")


@register_rule(
    id="scale_candidate", title="Scale candidate", tier="performance", levels=("campaign", "adset"), cadence="daily",
    params=ScaleParams, actions=("budget_up", "open_agent"), playbook_ref=PLAYBOOK,
    description="Likely well above break-even and limited by its budget: a candidate for a budget raise.",
)
def scale_candidate(ctx: RuleContext, e: Entity, p: ScaleParams) -> Hit | None:
    budget = e.attrs.get("daily_budget")
    if not budget or ctx.learning(e):
        return None
    budget_inr = float(budget) / 100.0
    spend3, k, value = _m(e, "3d", "spend"), _m(e, "3d", "purchases"), _m(e, "3d", "purchase_value")
    full_days = max(spend3 - _m(e, "today", "spend"), 0.0) / 2.0
    if spend3 < p.min_spend_inr or full_days < p.budget_use * budget_inr:
        return None
    g, st = ctx.guard(e), ctx.stats
    line = p.multiple_of_be * g.be_roas_platform
    p_above = 1.0 - S.prob_roas_below(line, k, spend3, value, ctx.prior_rate(e), ctx.prior_aov(e), st.prior_events, st.prior_orders)
    if p_above < st.confidence:
        return None
    roas = value / spend3
    from seleric_swarm.services.live_monitor.adapters.meta import raise_pct

    extra = budget_inr * raise_pct(ctx.tenant) / 100.0
    return Hit("info", f"3-day ROAS {roas:.2f} against break-even {g.be_roas_platform:.2f}, spending its full {_inr(budget_inr)} budget.",
               {"roas_3d": round(roas, 2), "be_roas": round(g.be_roas_platform, 2), "p_above_line": round(p_above, 3),
                "line": round(line, 2), "daily_budget": budget_inr, "avg_spend_full_days": round(full_days, 0)},
               impact_inr=extra * max(roas / g.be_roas_platform - 1.0, 0.0), confidence=round(p_above, 3),
               actions=("budget_up", "open_agent"))

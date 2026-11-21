"""Playbook compliance rules. Setup rules apply only to campaigns whose name matches the tenant's
``compliance_name_pattern`` (campaigns built to the playbook); status, review and pixel rules apply everywhere."""

from __future__ import annotations

import re
from datetime import timedelta

from pydantic import Field

from seleric_swarm.services.live_monitor.core.context import RuleContext
from seleric_swarm.services.live_monitor.core.model import Entity, Hit
from seleric_swarm.services.live_monitor.core.registry import Params, register_rule

PLAYBOOK = "Meta Ads Playbook"


class NoParams(Params):
    pass


def in_scope(ctx: RuleContext, e: Entity) -> bool:
    pattern = ctx.tenant.expectations.compliance_name_pattern
    if not pattern:
        return True
    campaign = ctx.snapshot.get(e.campaign_id) if e.level != "campaign" else e
    return bool(campaign and re.search(pattern, campaign.name))


def _parents_active(ctx: RuleContext, e: Entity) -> bool:
    return all(n.active for n in ctx.snapshot.lineage(e)[1:] if n.level in ("campaign", "adset"))


# ------------------------------------------------------------------------------------------------ status

class StatusParams(Params):
    review_hours: float = Field(24, ge=1, le=168, description="Pending review longer than this is a warning")


@register_rule(
    id="status_not_active", title="Not delivering: rejected, issues or stuck in review", tier="compliance",
    levels=("adset", "ad"), cadence="5m", params=StatusParams, actions=("open_agent",), playbook_ref=f"{PLAYBOOK} §6 Launch",
    description="An ad or ad set under an active parent is disapproved, has issues, lacks billing, or is stuck in review.",
)
def status_not_active(ctx: RuleContext, e: Entity, p: StatusParams) -> Hit | None:
    if not _parents_active(ctx, e):
        return None
    status = e.effective_status
    ev = {"effective_status": status, "configured_status": e.status}
    if status in ("DISAPPROVED", "WITH_ISSUES", "PENDING_BILLING_INFO"):
        reason = "; ".join(e.attrs.get("issues") or []) or _feedback(e)
        return Hit("critical", f"{e.level.capitalize()} is {status.replace('_', ' ').lower()}" + (f": {reason}" if reason else "."),
                   {**ev, "reason": reason}, actions=("open_agent",))
    if status in ("PENDING_REVIEW", "IN_PROCESS") and e.created_at is not None:
        age = ctx.now_local - e.created_at.astimezone(ctx.now_local.tzinfo)
        if age > timedelta(hours=p.review_hours):
            return Hit("warning", f"In review for {age.total_seconds() / 3600:.0f} hours.", {**ev, "hours": round(age.total_seconds() / 3600, 1)},
                       actions=("open_agent",))
    return None


def _feedback(e: Entity) -> str:
    fb = e.attrs.get("review_feedback") or {}
    parts = []
    for v in fb.values():
        if isinstance(v, dict):
            parts.extend(f"{k}: {x}" for k, x in v.items())
    return "; ".join(parts)


@register_rule(
    id="ad_review_issue", title="Delivering ad with review issues", tier="compliance", levels=("ad",), cadence="5m",
    params=NoParams, actions=("open_agent",), playbook_ref=f"{PLAYBOOK} §6 Launch",
    description="An ACTIVE ad carries Meta issues or review feedback (often limited delivery on some placements).",
)
def ad_review_issue(ctx: RuleContext, e: Entity, p: NoParams) -> Hit | None:
    if not e.active:
        return None
    issues, fb = list(e.attrs.get("issues") or []), _feedback(e)
    if not issues and not fb:
        return None
    detail = "; ".join(issues) or fb
    return Hit("critical", f"Meta flagged this ad: {detail}", {"issues": issues, "review_feedback": e.attrs.get("review_feedback")},
               actions=("open_agent",))


# ------------------------------------------------------------------------------------------------- setup

@register_rule(
    id="missing_campaign_label", title="Campaign label missing", tier="compliance", levels=("campaign",), cadence="5m",
    params=NoParams, actions=("set_label",), playbook_ref=f"{PLAYBOOK} §2 Naming",
    description="Playbook campaigns carry their test label so reporting can group them.",
)
def missing_campaign_label(ctx: RuleContext, e: Entity, p: NoParams) -> Hit | None:
    if not in_scope(ctx, e) or e.attrs.get("labels"):
        return None
    return Hit("warning", "No campaign label: reporting cannot group this test.", {"labels": []}, actions=("set_label",))


@register_rule(
    id="missing_ig_stream", title="Instagram feed placement missing", tier="compliance", levels=("adset",), cadence="5m",
    params=NoParams, actions=("add_ig_stream",), playbook_ref=f"{PLAYBOOK} §3 Placements",
    description="Manual placements include Instagram but not the Instagram feed.",
)
def missing_ig_stream(ctx: RuleContext, e: Entity, p: NoParams) -> Hit | None:
    t = e.attrs.get("targeting") or {}
    platforms, positions = t.get("publisher_platforms"), t.get("instagram_positions")
    if not in_scope(ctx, e) or not platforms or "instagram" not in platforms or not positions or "stream" in positions:
        return None
    return Hit("warning", f"Instagram is on but the feed is not (placements: {', '.join(positions)}).",
               {"instagram_positions": positions}, actions=("add_ig_stream",))


@register_rule(
    id="wrong_pixel_or_event", title="Wrong pixel or conversion event", tier="compliance", levels=("adset",), cadence="5m",
    params=NoParams, actions=("open_agent",), playbook_ref=f"{PLAYBOOK} §5 Tracking",
    description="A conversion ad set optimises on another pixel or event than the brand's.",
)
def wrong_pixel_or_event(ctx: RuleContext, e: Entity, p: NoParams) -> Hit | None:
    exp = ctx.tenant.expectations
    po = e.attrs.get("promoted_object") or {}
    if not po.get("pixel_id"):
        return None
    problems = []
    if exp.pixel_id and str(po["pixel_id"]) != str(exp.pixel_id):
        problems.append(f"pixel {po['pixel_id']} instead of {exp.pixel_id}")
    if exp.conversion_event and po.get("custom_event_type") and po["custom_event_type"] != exp.conversion_event:
        problems.append(f"event {po['custom_event_type']} instead of {exp.conversion_event}")
    if not problems:
        return None
    return Hit("critical", "Optimising on the wrong signal: " + ", ".join(problems) + ".", {"promoted_object": po},
               actions=("open_agent",))


@register_rule(
    id="lifecycle_restricted", title="Existing-customer budget cap set", tier="compliance", levels=("adset",), cadence="5m",
    params=NoParams, actions=("open_agent",), playbook_ref=f"{PLAYBOOK} §3 Audience",
    description="Prospecting ad sets in playbook campaigns should not cap spend on existing customers.",
)
def lifecycle_restricted(ctx: RuleContext, e: Entity, p: NoParams) -> Hit | None:
    pct = e.attrs.get("existing_customer_budget_percentage")
    if not in_scope(ctx, e) or pct in (None, ""):
        return None
    return Hit("warning", f"Existing-customer budget cap is set to {pct}%.", {"existing_customer_budget_percentage": pct},
               actions=("open_agent",))


def _attribution(spec: list[dict]) -> set[tuple[str, int]]:
    return {(str(s.get("event_type")), int(s.get("window_days") or 0)) for s in spec or []}


@register_rule(
    id="attribution_mismatch", title="Attribution window differs", tier="compliance", levels=("adset",), cadence="5m",
    params=NoParams, actions=("open_agent",), playbook_ref=f"{PLAYBOOK} §5 Tracking",
    description="The ad set's attribution setting differs from the brand's standard, so its results are not comparable.",
)
def attribution_mismatch(ctx: RuleContext, e: Entity, p: NoParams) -> Hit | None:
    expected = _attribution([a.model_dump() for a in ctx.tenant.expectations.attribution])
    actual = _attribution(e.attrs.get("attribution_spec") or [])
    if not in_scope(ctx, e) or not expected or not actual or actual == expected:
        return None
    fmt = lambda s: ", ".join(f"{t.split('_')[0].lower()} {d}d" for t, d in sorted(s))
    return Hit("warning", f"Attribution is {fmt(actual)}; the standard is {fmt(expected)}.",
               {"attribution_spec": e.attrs.get("attribution_spec")}, actions=("open_agent",))


@register_rule(
    id="missing_display_link_or_headline", title="Display link or headline missing", tier="compliance", levels=("ad",),
    cadence="5m", params=NoParams, actions=("swap_creative",), playbook_ref=f"{PLAYBOOK} §4 Creative",
    description="Link ads in playbook campaigns show the brand domain as display link and carry a headline.",
)
def missing_display_link_or_headline(ctx: RuleContext, e: Entity, p: NoParams) -> Hit | None:
    if not in_scope(ctx, e) or not e.attrs.get("is_link_creative") or e.attrs.get("has_asset_feed"):
        return None
    domain = ctx.tenant.expectations.display_domain
    missing = []
    shown = str(e.attrs.get("display_link") or "")
    if not shown or (domain and domain.lower() not in shown.lower()):
        missing.append(f"display link ({shown or 'empty'})")
    if not e.attrs.get("headline"):
        missing.append("headline")
    if not missing:
        return None
    return Hit("info", "Creative is missing: " + ", ".join(missing) + ".",
               {"display_link": shown or None, "headline": e.attrs.get("headline"), "expected_domain": domain},
               actions=("swap_creative",))

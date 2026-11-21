"""Meta source adapter (reads through Seleric MCP ``meta_*`` tools) and the Meta one-click actions."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from seleric_swarm.services.live_monitor.core.model import (
    ADDITIVE,
    WINDOWS,
    Entity,
    FieldDef,
    Snapshot,
    derive,
    sum_metrics,
)
from seleric_swarm.services.live_monitor.core.registry import (
    ActionDef,
    ActionInput,
    ActionPlan,
    ToolCall,
    register_action,
    register_adapter,
)
from seleric_swarm.services.live_monitor.mcp import McpClient

_log = logging.getLogger("seleric.live_monitor.meta")
PLATFORM = "meta"
# Delivering, plus the states that need a person (an ACTIVE parent with a disapproved child is the compliance case).
WATCH_STATUSES = ["ACTIVE", "DISAPPROVED", "WITH_ISSUES", "PENDING_REVIEW", "IN_PROCESS", "PENDING_BILLING_INFO"]
_PURCHASE = ("offsite_conversion.fb_pixel_purchase", "purchase", "omni_purchase")
_ATC = ("offsite_conversion.fb_pixel_add_to_cart", "add_to_cart", "omni_add_to_cart")
_IC = ("offsite_conversion.fb_pixel_initiate_checkout", "initiate_checkout", "omni_initiated_checkout")
_INSIGHT_FIELDS = ["ad_id", "adset_id", "campaign_id", "spend", "impressions", "reach", "frequency",
                   "inline_link_clicks", "actions", "action_values"]
# How long a window's insights stay fresh before the next tick refetches them.
WINDOW_TTL_S = {"3d": 3600, "7d": 3600, "prev7d": 86400}
_LEVEL_TOOL = {"campaign": "campaigns", "adset": "adsets", "ad": "ads"}


def _action(row: dict[str, Any], key: str, names: tuple[str, ...]) -> float:
    by_type = {a.get("action_type"): a.get("value") for a in row.get(key) or []}
    for n in names:
        if n in by_type:
            try:
                return float(by_type[n])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalise_insight(row: dict[str, Any]) -> dict[str, float]:
    return {
        "spend": _f(row.get("spend")),
        "impressions": _f(row.get("impressions")),
        "link_clicks": _f(row.get("inline_link_clicks")),
        "lpv": _action(row, "actions", ("landing_page_view", "omni_landing_page_view")),
        "purchases": _action(row, "actions", _PURCHASE),
        "purchase_value": _action(row, "action_values", _PURCHASE),
        "atc": _action(row, "actions", _ATC),
        "ic": _action(row, "actions", _IC),
        "reach": _f(row.get("reach")),
        "frequency": _f(row.get("frequency")),
    }


def roll_up(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float | None]]]:
    """Ad rows -> {level: {id: metrics}} for ad, adset, campaign and account (key "")."""
    groups: dict[str, dict[str, list[dict[str, float]]]] = {"ad": {}, "adset": {}, "campaign": {}, "account": {}}
    for r in rows:
        m = normalise_insight(r)
        for level, key in (("ad", r.get("ad_id")), ("adset", r.get("adset_id")), ("campaign", r.get("campaign_id")), ("account", "")):
            if key is not None:
                groups[level].setdefault(str(key), []).append(m)
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    for level, by_id in groups.items():
        out[level] = {}
        for key, ms in by_id.items():
            agg = sum_metrics(ms)
            if level == "ad" and len(ms) == 1:
                agg["reach"], agg["frequency"] = ms[0]["reach"] or None, ms[0]["frequency"] or None
            out[level][key] = agg
    return out


def _parse_time(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _hour_of(row: dict[str, Any]) -> int | None:
    raw = str(row.get("hourly_stats_aggregated_by_advertiser_time_zone") or "")
    m = re.match(r"^(\d{2}):", raw)
    return int(m.group(1)) if m else None


def campaign_entity(tenant_id: str, account: str, c: dict[str, Any]) -> Entity:
    return Entity(
        PLATFORM, tenant_id, "campaign", str(c["id"]), c.get("name") or c["id"], parent_id=account, campaign_id=str(c["id"]),
        status=c.get("status") or "", effective_status=c.get("effective_status") or "", created_at=_parse_time(c.get("created_time")),
        attrs={
            "objective": c.get("objective"), "bid_strategy": c.get("bid_strategy"),
            "daily_budget": _int(c.get("daily_budget")), "lifetime_budget": _int(c.get("lifetime_budget")),
            "labels": [x.get("name") for x in c.get("adlabels") or [] if x.get("name")],
            "budget_type": "cbo" if _int(c.get("daily_budget")) or _int(c.get("lifetime_budget")) else "abo",
        },
    )


def adset_entity(tenant_id: str, s: dict[str, Any]) -> Entity:
    targeting = s.get("targeting") or {}
    return Entity(
        PLATFORM, tenant_id, "adset", str(s["id"]), s.get("name") or s["id"], parent_id=str(s.get("campaign_id")),
        campaign_id=str(s.get("campaign_id")), adset_id=str(s["id"]), status=s.get("status") or "",
        effective_status=s.get("effective_status") or "", created_at=_parse_time(s.get("created_time")),
        attrs={
            "daily_budget": _int(s.get("daily_budget")), "lifetime_budget": _int(s.get("lifetime_budget")),
            "optimization_goal": s.get("optimization_goal"), "bid_strategy": s.get("bid_strategy"),
            "promoted_object": s.get("promoted_object") or {}, "attribution_spec": s.get("attribution_spec") or [],
            "learning_stage_info": s.get("learning_stage_info") or {},
            "existing_customer_budget_percentage": s.get("existing_customer_budget_percentage"),
            "targeting": {"publisher_platforms": targeting.get("publisher_platforms"),
                          "instagram_positions": targeting.get("instagram_positions"),
                          "facebook_positions": targeting.get("facebook_positions")},
        },
    )


def ad_entity(tenant_id: str, a: dict[str, Any]) -> Entity:
    creative = a.get("creative") or {}
    spec = creative.get("object_story_spec") or {}
    link = spec.get("link_data") or {}
    return Entity(
        PLATFORM, tenant_id, "ad", str(a["id"]), a.get("name") or a["id"], parent_id=str(a.get("adset_id")),
        campaign_id=str(a.get("campaign_id")), adset_id=str(a.get("adset_id")), status=a.get("status") or "",
        effective_status=a.get("effective_status") or "", created_at=_parse_time(a.get("created_time")),
        attrs={
            "creative_id": creative.get("id"), "is_link_creative": bool(link), "has_asset_feed": bool(creative.get("asset_feed_spec")),
            "link": link.get("link"), "display_link": link.get("caption"), "headline": link.get("name"),
            "message": link.get("message"), "image_hash": link.get("image_hash"), "page_id": spec.get("page_id"),
            "call_to_action": link.get("call_to_action"),
            "issues": [i.get("error_summary") or i.get("error_message") or str(i) for i in a.get("issues_info") or []],
            "review_feedback": a.get("ad_review_feedback") or {},
        },
    )


class MetaAdapter:
    platform = PLATFORM

    def __init__(self, client: McpClient) -> None:
        self.client = client
        self._windows: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
        self._structure: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}

    @staticmethod
    def _today(tenant: Any) -> datetime:
        return datetime.now(ZoneInfo(tenant.timezone))

    def _range(self, tenant: Any, window: str) -> dict[str, str]:
        today = self._today(tenant).date()
        start, end = {"3d": (2, 0), "7d": (6, 0), "prev7d": (9, 3)}[window]
        return {"since": (today - timedelta(days=start)).isoformat(), "until": (today - timedelta(days=end)).isoformat()}

    async def _insights(self, tenant: Any, **kw: Any) -> list[dict[str, Any]]:
        args = {"object_id": tenant.account_id, "brand_id": str(tenant.brand_id), "fields": kw.pop("fields", _INSIGHT_FIELDS), **kw}
        return await self.client.paginate("meta_insights_live", args, page_size=500)

    async def _list(self, tenant: Any, tool: str, errors: list[str]) -> list[dict[str, Any]]:
        key = (tenant.tenant_id, tool)
        args: dict[str, Any] = {"account_id": tenant.account_id, "status": ["ACTIVE"] if tool == "meta_campaigns_list" else WATCH_STATUSES}
        try:
            rows = await self.client.paginate(tool, args)
            self._structure[key] = (time.monotonic(), rows)
            return rows
        except Exception as exc:
            cached = self._structure.get(key)
            if cached and time.monotonic() - cached[0] < tenant.structure_cache_ttl_min * 60:
                errors.append(f"{tool}: {exc} (using the list from {int((time.monotonic() - cached[0]) / 60)} min ago)")
                return cached[1]
            raise

    async def _window(self, tenant: Any, window: str, errors: list[str]) -> list[dict[str, Any]] | None:
        key = (tenant.tenant_id, window)
        cached = self._windows.get(key)
        if cached and time.monotonic() - cached[0] < WINDOW_TTL_S[window]:
            return cached[1]
        try:
            rows = await self._insights(tenant, level="ad", time_range=self._range(tenant, window))
        except Exception as exc:
            errors.append(f"insights {window}: {exc}")
            return cached[1] if cached else None
        self._windows[key] = (time.monotonic(), rows)
        return rows

    async def discover(self, tenant: Any) -> Snapshot:
        errors: list[str] = []
        campaigns, adsets, ads = await asyncio.gather(
            self._list(tenant, "meta_campaigns_list", errors), self._list(tenant, "meta_adsets_list", errors),
            self._list(tenant, "meta_ads_list", errors),
        )
        today_rows, hourly_rows = await asyncio.gather(
            self._insights(tenant, level="ad", date_preset="today"),
            self._insights(tenant, level="adset", date_preset="today", hourly=True,
                           fields=["adset_id", "campaign_id", "spend", "impressions"]),
        )
        windows: dict[str, list[dict[str, Any]] | None] = {"today": today_rows}
        for w in WINDOWS[1:]:
            windows[w] = await self._window(tenant, w, errors)

        excluded = set(tenant.exclude_entity_ids)
        account = Entity(PLATFORM, tenant.tenant_id, "account", tenant.account_id, tenant.name or tenant.account_id,
                         status="ACTIVE", effective_status="ACTIVE")
        live_campaigns = {str(c["id"]) for c in campaigns if str(c["id"]) not in excluded}
        entities = [account] + [campaign_entity(tenant.tenant_id, tenant.account_id, c) for c in campaigns if str(c["id"]) in live_campaigns]
        live_adsets = set()
        for s in adsets:
            if str(s.get("campaign_id")) in live_campaigns and str(s["id"]) not in excluded:
                entities.append(adset_entity(tenant.tenant_id, s))
                live_adsets.add(str(s["id"]))
        for a in ads:
            if str(a.get("adset_id")) in live_adsets and str(a["id"]) not in excluded:
                entities.append(ad_entity(tenant.tenant_id, a))

        empty = derive({k: 0.0 for k in ADDITIVE})
        for window, rows in windows.items():
            if rows is None:
                continue
            rolled = roll_up(rows)
            for e in entities:
                key = "" if e.level == "account" else e.id
                e.metrics[window] = rolled[e.level].get(key, dict(empty))

        hourly: dict[str, dict[int, dict[str, float]]] = {}
        for r in hourly_rows:
            h = _hour_of(r)
            if h is None:
                continue
            for key in (str(r.get("adset_id")), str(r.get("campaign_id"))):
                slot = hourly.setdefault(key, {}).setdefault(h, {"spend": 0.0, "impressions": 0.0})
                slot["spend"] += _f(r.get("spend"))
                slot["impressions"] += _f(r.get("impressions"))
        return Snapshot(tenant.tenant_id, PLATFORM, self._today(tenant), entities, hourly, errors)

    async def daily_history(self, tenant: Any, days: int) -> list[dict[str, Any]]:
        today = self._today(tenant).date()
        rows = await self._insights(
            tenant, level="ad", time_increment="1",
            time_range={"since": (today - timedelta(days=days)).isoformat(), "until": (today - timedelta(days=1)).isoformat()},
        )
        out = []
        for r in rows:
            m = normalise_insight(r)
            out.append({"date": r.get("date_start"), "ad_id": str(r.get("ad_id")), "adset_id": str(r.get("adset_id")),
                        "campaign_id": str(r.get("campaign_id")), **m})
        return out

    async def hourly_profile(self, tenant: Any, days: int) -> dict[int, dict[str, float]]:
        today = self._today(tenant).date()
        rows = await self._insights(
            tenant, hourly=True, fields=["spend", "impressions"],
            time_range={"since": (today - timedelta(days=days)).isoformat(), "until": (today - timedelta(days=1)).isoformat()},
        )
        out: dict[int, dict[str, float]] = {h: {"spend": 0.0, "impressions": 0.0} for h in range(24)}
        for r in rows:
            h = _hour_of(r)
            if h is not None:
                out[h]["spend"] += _f(r.get("spend"))
                out[h]["impressions"] += _f(r.get("impressions"))
        return out

    def field_catalogue(self) -> list[FieldDef]:
        return field_catalogue()


def field_catalogue() -> list[FieldDef]:
    every = ("account", "campaign", "adset", "ad")
    delivery = ("campaign", "adset", "ad")
    f = [
        FieldDef("name", "string", delivery, "Name"),
        FieldDef("effective_status", "string", delivery, "Delivery status"),
        FieldDef("attrs.daily_budget", "number", ("campaign", "adset"), "Daily budget", "paise"),
        FieldDef("attrs.labels", "string_list", ("campaign",), "Campaign labels"),
        FieldDef("attrs.objective", "string", ("campaign",), "Objective"),
        FieldDef("attrs.optimization_goal", "string", ("adset",), "Optimisation goal"),
        FieldDef("attrs.promoted_object.pixel_id", "string", ("adset",), "Pixel id"),
        FieldDef("attrs.promoted_object.custom_event_type", "string", ("adset",), "Conversion event"),
        FieldDef("attrs.learning_stage_info.status", "string", ("adset",), "Learning stage"),
        FieldDef("attrs.targeting.instagram_positions", "string_list", ("adset",), "Instagram placements"),
        FieldDef("attrs.targeting.publisher_platforms", "string_list", ("adset",), "Platforms"),
        FieldDef("attrs.display_link", "string", ("ad",), "Display link"),
        FieldDef("attrs.headline", "string", ("ad",), "Headline"),
        FieldDef("attrs.link", "string", ("ad",), "Destination URL"),
    ]
    units = {"spend": "INR", "purchase_value": "INR", "cpm": "INR", "cpa": "INR", "aov": "INR", "ctr_link": "%", "cvr_lpv": "%", "roas": "x"}
    labels = {"spend": "Spend", "impressions": "Impressions", "link_clicks": "Link clicks", "lpv": "Landing-page views",
              "purchases": "Purchases", "purchase_value": "Purchase value", "atc": "Add to cart", "ic": "Initiate checkout",
              "cpm": "CPM", "ctr_link": "Link CTR", "cpa": "CPA", "roas": "ROAS", "aov": "AOV",
              "lpv_per_click": "LPV per click", "cvr_lpv": "Purchase per LPV", "frequency": "Frequency"}
    for w in WINDOWS:
        for key, label in labels.items():
            levels = ("ad",) if key == "frequency" else every
            f.append(FieldDef(f"metrics.{w}.{key}", "number", levels, f"{label} ({w})", units.get(key, "")))
    return f


register_adapter(PLATFORM, MetaAdapter)


# ----------------------------------------------------------------------------------------------------- actions

# Seleric MCP refuses a raise above +10 % of the live budget (owner policy, seleric_mcp.ads.meta.policy.MAX_RAISE_PCT).
MCP_MAX_RAISE_PCT = 10


def raise_pct(tenant: Any) -> int:
    return min(int(tenant.actions.budget_step_pct), MCP_MAX_RAISE_PCT)


def _budget_owner(e: Entity) -> tuple[str, str]:
    """The entity whose daily budget an action on ``e`` changes: an ad set with its own budget, else its campaign."""
    if e.level in ("campaign", "adset"):
        return e.level, e.id
    return "adset", str(e.adset_id)


async def _live(client: McpClient, level: str, entity_id: str, tenant: Any) -> dict[str, Any]:
    out = await client.call(f"meta_{_LEVEL_TOOL[level]}_get", {f"{level}_id": entity_id, "brand_id": str(tenant.brand_id)})
    return out.get("entity") or {}


async def _pause(client: McpClient, e: Entity, tenant: Any, inputs: dict[str, str]) -> ActionPlan:
    if e.level not in _LEVEL_TOOL:
        raise ValueError("only a campaign, ad set or ad can be paused")
    live = await _live(client, e.level, e.id, tenant)
    return ActionPlan(
        f"Pause {e.level} {e.name}",
        (ToolCall(f"meta_{_LEVEL_TOOL[e.level]}_set_status", {f"{e.level}_id": e.id, "status": "PAUSED", "brand_id": str(tenant.brand_id)}),),
        current={"status": live.get("status")}, change={"status": "PAUSED"},
    )


def _budget(direction: int):
    async def build(client: McpClient, e: Entity, tenant: Any, inputs: dict[str, str]) -> ActionPlan:
        level, owner = _budget_owner(e)
        live = await _live(client, level, owner, tenant)
        current = _int(live.get("daily_budget"))
        if not current and level == "adset":
            level, owner = "campaign", str(e.campaign_id)
            live = await _live(client, level, owner, tenant)
            current = _int(live.get("daily_budget"))
        if not current:
            raise ValueError("no daily budget on this entity, its ad set or its campaign")
        cap = int(tenant.actions.max_daily_budget_minor)
        if direction > 0:
            new = min(current * (100 + raise_pct(tenant)) // 100 // 100 * 100, cap)
            if new <= current:
                raise ValueError(f"no headroom: the daily budget is already at the cap ({cap // 100:,} INR)")
        else:
            new = int(round(current * (100 - tenant.actions.budget_step_pct) / 100 / 100) * 100)
            new = max(new, int(tenant.actions.min_daily_budget_minor))
            if new >= current:
                raise ValueError(f"the daily budget is already at the floor ({new // 100:,} INR)")
        tool = "meta_campaigns_update_budget" if level == "campaign" else "meta_adsets_update_budget"
        return ActionPlan(
            f"{'Raise' if direction > 0 else 'Lower'} the {level} daily budget from {current // 100:,} to {new // 100:,} INR",
            (ToolCall(tool, {f"{level}_id": owner, "daily_budget": new, "brand_id": str(tenant.brand_id)}),),
            current={"daily_budget": current, "level": level, "id": owner}, change={"daily_budget": new},
        )

    return build


def _label_from_name(e: Entity, tenant: Any) -> dict[str, str]:
    m = re.match(tenant.expectations.label_from_name, e.name or "")
    return {"campaign_label": m.group(1) if m and m.groups() else ""}


async def _set_label(client: McpClient, e: Entity, tenant: Any, inputs: dict[str, str]) -> ActionPlan:
    label = (inputs.get("campaign_label") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _\-]{1,79}", label):
        raise ValueError("campaign label must be 2-80 letters, digits, spaces, - or _")
    return ActionPlan(
        f"Attach label {label} to campaign {e.name}",
        (ToolCall("meta_campaigns_set_label", {"campaign_id": e.id, "campaign_label": label, "account_id": tenant.account_id}),),
        current={"labels": e.attrs.get("labels") or []}, change={"campaign_label": label},
    )


async def _add_ig_stream(client: McpClient, e: Entity, tenant: Any, inputs: dict[str, str]) -> ActionPlan:
    live = await _live(client, "adset", e.id, tenant)
    targeting = dict(live.get("targeting") or {})
    positions = list(targeting.get("instagram_positions") or [])
    if "stream" in positions:
        raise ValueError("Instagram Feed (stream) is already selected")
    targeting["instagram_positions"] = positions + ["stream"]
    return ActionPlan(
        f"Add Instagram Feed to ad set {e.name}",
        (ToolCall("meta_adsets_update_targeting", {"adset_id": e.id, "targeting": targeting, "brand_id": str(tenant.brand_id)}),),
        current={"instagram_positions": positions}, change={"instagram_positions": targeting["instagram_positions"]},
    )


def _creative_prefill(e: Entity, tenant: Any) -> dict[str, str]:
    return {"headline": e.attrs.get("headline") or "", "display_link": e.attrs.get("display_link") or tenant.expectations.display_domain or ""}


async def _swap_creative(client: McpClient, e: Entity, tenant: Any, inputs: dict[str, str]) -> ActionPlan:
    a = e.attrs
    if not a.get("is_link_creative") or not a.get("image_hash") or not a.get("page_id"):
        raise ValueError("only single-image link creatives can be rebuilt here; open it in Marketing Agent")
    headline = (inputs.get("headline") or "").strip()
    display = (inputs.get("display_link") or "").strip()
    if not headline or len(headline) > 255:
        raise ValueError("headline is required (at most 255 characters)")
    if not re.fullmatch(r"[A-Za-z0-9.\-]{3,100}", display):
        raise ValueError("display link must be a domain such as tiltingheads.com")
    create = {
        "account_id": tenant.account_id, "name": f"{e.name} (display link)", "page_id": a["page_id"],
        "image_hash": a["image_hash"], "message": a.get("message"), "headline": headline, "display_link": display,
        "link": a.get("link"), "call_to_action": a.get("call_to_action"),
    }
    return ActionPlan(
        f"Rebuild the creative of ad {e.name} with headline and display link, then swap it onto the ad",
        (ToolCall("meta_creatives_create_image", {k: v for k, v in create.items() if v is not None}),
         ToolCall("meta_ads_update", {"ad_id": e.id, "brand_id": str(tenant.brand_id)}, from_steps={"creative_id": (0, "entity.id")})),
        current={"headline": a.get("headline"), "display_link": a.get("display_link")},
        change={"headline": headline, "display_link": display},
    )


register_action(ActionDef("open_agent", "Open in Marketing Agent", PLATFORM, ("account", "campaign", "adset", "ad"), "link"))
register_action(ActionDef("pause", "Pause", PLATFORM, ("campaign", "adset", "ad"), "delivery", _pause))
register_action(ActionDef("budget_down", "Lower budget", PLATFORM, ("campaign", "adset", "ad"), "spend", _budget(-1)))
register_action(ActionDef("budget_up", "Raise budget", PLATFORM, ("campaign", "adset"), "spend", _budget(+1)))
register_action(ActionDef("set_label", "Attach SKU label", PLATFORM, ("campaign",), "low", _set_label,
                          (ActionInput("campaign_label", "Label", 80),), _label_from_name))
register_action(ActionDef("add_ig_stream", "Add Instagram Feed", PLATFORM, ("adset",), "low", _add_ig_stream))
register_action(ActionDef("swap_creative", "Fix headline / display link", PLATFORM, ("ad",), "low", _swap_creative,
                          (ActionInput("headline", "Headline", 255), ActionInput("display_link", "Display link", 100)),
                          _creative_prefill))

from __future__ import annotations

from typing import Any

import pytest

from seleric_swarm.services.domain_health.monitor_config import ConfigError, VersionConflict
from seleric_swarm.services.live_monitor import config as C
from seleric_swarm.services.live_monitor.core import schema
from seleric_swarm.services.live_monitor.core.registry import ACTIONS

from .helpers import AD, ADSET, CAMPAIGN, cfg, world


def test_defaults_validate_and_schema_lists_every_rule():
    assert cfg().tenants[0].brand_id == 20
    s = schema.build()
    ids = {r["id"] for r in s["rules"]}
    assert {"roas_below", "spend_pacing", "missing_ig_stream"} <= ids
    assert all("properties" in r["params_schema"] for r in s["rules"])
    assert s["platforms"]["meta"]["fields"]


@pytest.mark.parametrize("change, message", [
    ({"rules": {"nope": {}}}, "no such rule"),
    ({"rules": {"roas_below": {"params": {"min_spend_inr": -1}}}}, "min_spend_inr"),
    ({"rules": {"roas_below": {"params": {"typo": 1}}}}, "typo"),
    ({"custom_rules": [{"id": "x1", "title": "bad field", "platform": "meta", "level": "ad",
                        "conditions": [{"field": "attrs.secret", "op": "eq", "value": "1"}]}]}, "not a field"),
    ({"custom_rules": [{"id": "x2", "title": "bad action", "platform": "meta", "level": "account",
                        "conditions": [{"field": "metrics.today.spend", "op": "gt", "value": 1}], "actions": ["pause"]}]}, "not available"),
])
def test_invalid_configs_are_rejected(change: dict, message: str):
    with pytest.raises(ConfigError, match=message):
        C.validate({**C.defaults(), **change})


def test_save_load_and_version_conflict(tmp_path):
    payload = {**C.defaults(), "rules": {"ctr_drop": {"enabled": False}}}
    entry = C.save(payload, expected_version=None, updated_by="me", base_dir=tmp_path)
    loaded, meta = C.load(tmp_path)
    assert meta["custom"] and not loaded.rule_setting("ctr_drop").enabled
    with pytest.raises(VersionConflict):
        C.save(payload, expected_version=entry["version"] - 1, updated_by="me", base_dir=tmp_path)
    assert C.reset(tmp_path)
    assert C.load(tmp_path)[1]["custom"] is False


class FakeClient:
    def __init__(self, live: dict[str, dict[str, Any]]):
        self.live = live

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        entity_id = next(v for k, v in args.items() if k.endswith("_id") and k != "brand_id")
        return {"success": True, "entity": self.live[entity_id]}


def _build(action_id: str, entity_id: str, live: dict, snap=None, inputs=None):
    import asyncio

    snap = snap or world()
    act = ACTIONS[("meta", action_id)]
    return asyncio.run(act.build(FakeClient(live), snap.get(entity_id), cfg().tenants[0], inputs or {}))


def test_budget_actions_respect_mcp_raise_limit_cap_and_floor():
    up = _build("budget_up", CAMPAIGN, {CAMPAIGN: {"daily_budget": "50000"}})
    assert up.change == {"daily_budget": 55000}  # +10 %: the MCP refuses more
    down = _build("budget_down", CAMPAIGN, {CAMPAIGN: {"daily_budget": "50000"}})
    assert down.change == {"daily_budget": 40000}
    with pytest.raises(ValueError, match="no headroom"):
        _build("budget_up", CAMPAIGN, {CAMPAIGN: {"daily_budget": "100000"}})
    with pytest.raises(ValueError, match="floor"):
        _build("budget_down", CAMPAIGN, {CAMPAIGN: {"daily_budget": "10000"}})


def test_budget_on_ad_goes_to_the_campaign_when_cbo():
    plan = _build("budget_down", AD, {ADSET: {"daily_budget": None}, CAMPAIGN: {"daily_budget": "100000"}})
    assert plan.calls[0].tool == "meta_campaigns_update_budget" and plan.current["id"] == CAMPAIGN


def test_set_label_and_swap_creative_plans():
    snap = world(campaign={"attrs": {"labels": []}})
    act = ACTIONS[("meta", "set_label")]
    assert act.prefill(snap.get(CAMPAIGN), cfg().tenants[0]) == {"campaign_label": "TH-477-SNUGSUIT"}
    plan = _build("set_label", CAMPAIGN, {}, snap, {"campaign_label": "TH-477-SNUGSUIT"})
    assert plan.calls[0].tool == "meta_campaigns_set_label"
    with pytest.raises(ValueError):
        _build("set_label", CAMPAIGN, {}, snap, {"campaign_label": "<script>"})
    swap = _build("swap_creative", AD, {}, None, {"headline": "SnugSuit", "display_link": "tiltingheads.com"})
    assert [c.tool for c in swap.calls] == ["meta_creatives_create_image", "meta_ads_update"]
    assert swap.calls[1].from_steps == {"creative_id": (0, "entity.id")}

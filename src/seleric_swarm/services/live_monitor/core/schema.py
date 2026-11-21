"""The schema the configuration page draws from: rules with their param schemas, actions, fields and operators per
platform. The page has no rule-specific code; a rule registered here appears there with its form."""

from __future__ import annotations

from typing import Any

from seleric_swarm.services.live_monitor.core import conditions
from seleric_swarm.services.live_monitor.core.model import CADENCES, LEVELS, SEVERITIES
from seleric_swarm.services.live_monitor.core.registry import (
    ACTIONS,
    ADAPTERS,
    RULES,
    ensure_loaded,
)


def _fields(platform: str) -> list[dict[str, Any]]:
    from seleric_swarm.services.live_monitor.adapters import meta

    cat = meta.field_catalogue() if platform == "meta" else []
    return [{"path": f.path, "type": f.type, "levels": list(f.levels), "label": f.label, "unit": f.unit} for f in cat]


def build() -> dict[str, Any]:
    ensure_loaded()
    from seleric_swarm.services.live_monitor.config import LiveMonitorConfig

    return {
        "levels": list(LEVELS), "severities": list(SEVERITIES), "cadences": CADENCES,
        "rules": [
            {"id": r.id, "title": r.title, "description": r.description, "tier": r.tier, "platforms": list(r.platforms),
             "levels": list(r.levels), "cadence": r.cadence, "actions": list(r.actions), "playbook_ref": r.playbook_ref,
             "efficiency": r.efficiency, "rollup": r.rollup, "supersedes": list(r.supersedes), "params_schema": r.params.model_json_schema(),
             "params_defaults": r.params().model_dump()}
            for r in sorted(RULES.values(), key=lambda r: (r.tier != "performance", r.id))
        ],
        "actions": [
            {"platform": a.platform, "id": a.id, "title": a.title, "levels": list(a.levels), "risk": a.risk,
             "inputs": [{"name": i.name, "label": i.label, "max_length": i.max_length} for i in a.inputs]}
            for a in ACTIONS.values()
        ],
        "platforms": {p: {"fields": _fields(p)} for p in sorted(ADAPTERS)},
        "conditions": {
            "operators": {k: list(v) for k, v in conditions.OPS_BY_TYPE.items()},
            "no_value_operators": sorted(conditions.NO_VALUE_OPS), "max_conditions": conditions.MAX_CONDITIONS,
            "refs": [f"guardrail.{g}" for g in conditions.GUARDRAIL_REFS]
            + [f"baseline.{m}.{s}" for m in conditions.BASELINE_METRICS for s in conditions.BASELINE_STATS],
        },
        "config_schema": LiveMonitorConfig.model_json_schema(),
    }

"""Workspace brand taken from the principal's context, applied to forecast fetches.

``build_metrics_query_args`` injects a deployment default brand when a query
names none. A workspace that declares ``workspace_config.brand_id`` must be
the brand the forecast reads — not that default, and not a caller-supplied
other brand.
"""

from __future__ import annotations

from typing import Any

_BRAND_KEYS = frozenset({"brand_id", "brand", "brand_name"})


def principal_brand_id(deps: Any) -> str | None:
    """Brand id from the mission context, when the workspace declares one."""
    ctx = getattr(deps, "context", None)
    cfg = getattr(ctx, "workspace_config", None) or {}
    if not isinstance(cfg, dict):
        return None
    for key in ("brand_id", "brand"):
        value = cfg.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def brand_filters(
    brand_id: str | None,
    filters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    """Drop any other brand filter and pin ``brand_id`` when the principal has one."""
    kept = [
        f
        for f in (filters or [])
        if str(f.get("dimension") or "").strip().lower() not in _BRAND_KEYS
    ]
    if not brand_id:
        return kept or None
    kept.append({"dimension": "brand_id", "operator": "equals", "values": [brand_id]})
    return kept

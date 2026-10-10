"""Entity selection for platform/channel/campaign/product/SKU forecasts.

Top-N by recent volume, sparse-entity refusal, optional ``rest`` remainder so
entity forecasts can be reconciled against the account total.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from seleric_swarm.forecasting.policies import EntityPolicy, ForecastPolicies
from seleric_swarm.services.mcp_query import dimension_value
from seleric_swarm.toolsets.semantic import raw_query_metric

_log = logging.getLogger("seleric.forecasting.entities")

_AGENT_ID = "v3_agent"
_VOLUME_LOOKBACK_DAYS = 90
_SUM_MISMATCH_WARN = 0.05


@dataclass
class EntitySelection:
    dimension: str
    values: list[str] = field(default_factory=list)
    volumes: dict[str, float] = field(default_factory=dict)
    account_total: float = 0.0
    refused: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    include_rest: bool = False
    rest_volume: float = 0.0
    policy: EntityPolicy | None = None

    @property
    def min_volume_share(self) -> float:
        return self.policy.min_volume_share if self.policy else 0.05


async def select_entities(
    *,
    mcp_client: Any,
    metric_id: str,
    dimension: str,
    as_of: date,
    policies: ForecastPolicies,
    requested_values: list[str] | None = None,
    agent_id: str = _AGENT_ID,
    filters: list[dict[str, Any]] | None = None,
) -> EntitySelection:
    """Pick top-N entities by volume over the last 90 days that clear the share floor.

    When ``requested_values`` is set (scope filters), those are ranked and
    sparse-refused individually instead of discovering the top-N.
    """
    target_policy = policies.target(metric_id)
    entity_policy = None
    if target_policy and target_policy.entities:
        entity_policy = target_policy.entities.get(dimension)
        if entity_policy is None:
            return EntitySelection(
                dimension=dimension,
                refused=[(dimension, "T_ENTITY_UNSUPPORTED")],
                warnings=["T_ENTITY_UNSUPPORTED"],
            )
    else:
        # No per-entity policy yet: allow with defaults (provisional entity path).
        entity_policy = EntityPolicy()

    end = as_of - timedelta(days=1)
    start = end - timedelta(days=_VOLUME_LOOKBACK_DAYS - 1)
    volumes, account_total, fetch_warn = await _volume_by_entity(
        mcp_client,
        metric_id=metric_id,
        dimension=dimension,
        start=start,
        end=end,
        agent_id=agent_id,
        filters=filters,
    )
    selection = EntitySelection(
        dimension=dimension,
        volumes=volumes,
        account_total=account_total,
        policy=entity_policy,
        warnings=list(fetch_warn),
    )
    if account_total <= 0 and not volumes:
        selection.warnings.append("Q_SPARSE_ENTITY:no_volume")
        return selection

    candidates = list(requested_values) if requested_values else sorted(
        volumes, key=lambda v: volumes.get(v, 0.0), reverse=True
    )
    # De-dupe while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for v in candidates:
        key = str(v).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)

    min_share = entity_policy.min_volume_share
    max_n = entity_policy.max_entities
    selected: list[str] = []
    for value in ordered:
        vol = volumes.get(value, 0.0)
        share = (vol / account_total) if account_total > 0 else 0.0
        if share < min_share and value not in (requested_values or []):
            selection.refused.append((value, f"Q_SPARSE_ENTITY:share={share:.0%}"))
            continue
        if requested_values and share < min_share:
            # Explicitly named entity still sparse → refuse that entity, offer aggregate.
            selection.refused.append((value, f"Q_SPARSE_ENTITY:share={share:.0%}"))
            selection.warnings.append(
                f"Q_SPARSE_ENTITY:{value}: refused sparse entity; use account aggregate"
            )
            continue
        selected.append(value)
        if len(selected) >= max_n:
            break

    selection.values = selected
    selected_vol = sum(volumes.get(v, 0.0) for v in selected)
    selection.rest_volume = max(0.0, account_total - selected_vol)
    if selection.rest_volume / account_total >= min_share if account_total else False:
        selection.include_rest = True
    if account_total > 0 and selected:
        covered = selected_vol / account_total
        if covered < 1.0 - _SUM_MISMATCH_WARN and not selection.include_rest:
            selection.warnings.append(
                f"entity_coverage={covered:.0%}: remainder below min share, not forecast separately"
            )
    return selection


def entity_filter(dimension: str, value: str) -> list[dict[str, Any]]:
    return [{"dimension": dimension, "operator": "equals", "values": [value]}]


def sum_mismatch_warning(
    account_total: float | None, entity_totals: list[float], *, threshold: float = _SUM_MISMATCH_WARN
) -> str | None:
    """Warn when entity forecast totals diverge from the account forecast by >threshold."""
    if account_total is None or not entity_totals:
        return None
    entity_sum = sum(entity_totals)
    if account_total == 0:
        return None
    gap = abs(entity_sum - account_total) / abs(account_total)
    if gap > threshold:
        return (
            f"account_vs_entity_sum_mismatch={gap:.0%} "
            f"(account={account_total:.4g}, entities={entity_sum:.4g})"
        )
    return None


async def _volume_by_entity(
    mcp_client: Any,
    *,
    metric_id: str,
    dimension: str,
    start: date,
    end: date,
    agent_id: str,
    filters: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, float], float, list[str]]:
    warnings: list[str] = []
    try:
        result = await raw_query_metric(
            mcp_client,
            agent_id=agent_id,
            metric_id=metric_id,
            start=start.isoformat(),
            end=end.isoformat(),
            grain="day",
            dimensions=[dimension],
            filters=filters,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("entity_volume_fetch_failed metric=%s dim=%s err=%s", metric_id, dimension, exc)
        return {}, 0.0, [f"entity_volume_fetch_failed:{type(exc).__name__}"]

    if result.get("error"):
        warnings.append(f"entity_volume_fetch_failed:{result.get('error')}")
        return {}, 0.0, warnings

    volumes: dict[str, float] = {}
    for row in result.get("rows") or []:
        if not isinstance(row, dict):
            continue
        ent = dimension_value(row, dimension)
        if ent is None or str(ent).strip() == "":
            continue
        raw = row.get(metric_id)
        if raw is None:
            continue
        try:
            volumes[str(ent)] = volumes.get(str(ent), 0.0) + float(raw)
        except (TypeError, ValueError):
            continue

    # Account total: prefer sum of entity volumes (same query); fall back to undimensioned.
    account_total = sum(volumes.values())
    if account_total <= 0:
        try:
            agg = await raw_query_metric(
                mcp_client,
                agent_id=agent_id,
                metric_id=metric_id,
                start=start.isoformat(),
                end=end.isoformat(),
                grain="day",
                filters=filters,
            )
            for row in agg.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                raw = row.get(metric_id)
                if raw is None:
                    continue
                try:
                    account_total += float(raw)
                except (TypeError, ValueError):
                    continue
        except Exception:  # noqa: BLE001
            pass
    return volumes, account_total, warnings

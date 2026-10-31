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
from seleric_swarm.toolsets._data import raw_query_metric

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
            entity_policy = EntityPolicy()
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

    if requested_values:
        requested_values = _resolve_named(requested_values, volumes, selection.warnings)
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
        if share < min_share and not requested_values:
            selection.refused.append((value, f"Q_SPARSE_ENTITY:share={share:.0%}"))
            continue
        if requested_values and vol <= 0:
            # Named by the user: forecast on any real volume; share only gates discovery.
            selection.refused.append((value, "Q_SPARSE_ENTITY:no_volume"))
            continue
        selected.append(value)
        if len(selected) >= max_n and not requested_values:
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


def _is_date(value: Any) -> bool:
    try:
        date.fromisoformat(str(value)[:10])
        return True
    except ValueError:
        return False


def _breakdown_value(row: dict[str, Any], dimension: str, metric_id: str) -> Any:
    """The row's value for *dimension*. The data layer may answer a breakdown through a conformed
    sibling (a view lacking the dimension answers via its twin), so when the requested key is absent the one
    remaining non-metric, non-date column is the breakdown."""
    value = dimension_value(row, dimension)
    if value is not None:
        return value
    rest = [
        v for k, v in row.items()
        if k != metric_id and not str(k).startswith(f"{metric_id}.")
        and not _is_date(v)
    ]
    return rest[0] if len(rest) == 1 else None


def _resolve_named(
    requested: list[str], volumes: dict[str, float], warnings: list[str]
) -> list[str]:
    """Map each named value onto the values the data actually has (exact, then case-insensitive,
    then containment either way), so a partial or re-cased name still finds its entity."""
    by_lower = {k.lower(): k for k in volumes}
    out: list[str] = []
    for raw in requested:
        name = str(raw).strip()
        if name in volumes:
            out.append(name)
        elif name.lower() in by_lower:
            out.append(by_lower[name.lower()])
        else:
            low = name.lower()
            hits = sorted(
                (k for k in volumes if low in k.lower() or (k.lower() in low and len(k) >= 3)),
                key=lambda k: -volumes[k],
            )
            if hits:
                warnings.append(f"entity_resolved:{name}->{hits[:5]}")
                out.extend(hits)
            else:
                out.append(name)
    return list(dict.fromkeys(out))


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
    except Exception as exc:
        _log.warning("entity_volume_fetch_failed metric=%s dim=%s err=%s", metric_id, dimension, exc)
        return {}, 0.0, [f"entity_volume_fetch_failed:{type(exc).__name__}"]

    if result.get("error"):
        warnings.append(f"entity_volume_fetch_failed:{result.get('error')}")
        return {}, 0.0, warnings

    volumes: dict[str, float] = {}
    for row in result.get("rows") or []:
        if not isinstance(row, dict):
            continue
        ent = _breakdown_value(row, dimension, metric_id)
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
        except Exception:
            pass
    return volumes, account_total, warnings

"""Live ad-platform envelopes as query results (D-244 bots).

Seleric MCP's live Meta/Google tools answer ``{success, platform, data: [...]}`` with numbers as strings. The agent's
evidence path (run_python, charts, grounding) only reads query results: ``query_id`` + ``columns`` + ``rows`` +
``provenance.metric_ids``, with a time column named ``<x>.<grain>``. This turns one into the other at the tool
boundary so a bot can compute over live rows. Only the bot read tools (``ADS_READ_TOOLS``) pass through here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

# Meta reports these in the account currency's minor unit (paise for INR); spend and insights are major units.
# Verified 2026-11-21 on brand 20: as paise, campaign-day spend sits at p50 0.35x / p90 1.06x of daily_budget.
# shortcut: assumes 100 minor units per major (INR, brand 20 only per B12); read account currency when multi-brand.
MINOR_UNIT_FIELDS = frozenset({"daily_budget", "lifetime_budget", "budget_remaining", "spend_cap"})
HOUR_FIELD = "hourly_stats_aggregated_by_advertiser_time_zone"  # "10:00:00 - 10:59:59", in the account's timezone


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _flat(row: dict[str, Any]) -> dict[str, Any]:
    """Action lists (``actions``, ``action_values``…: ``[{action_type, value}]``) become one column per type.
    A dot would read as a time column, so it becomes an underscore."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        pairs = value if isinstance(value, list) and value and all(isinstance(v, dict) and "action_type" in v for v in value) else None
        if pairs is None:
            out[key] = value
            continue
        for pair in pairs:
            out[f"{key}__{str(pair['action_type']).replace('.', '_')}"] = pair.get("value")
    return out


def _is_id(key: str) -> bool:
    return key == "id" or key.endswith("_id")


def as_query_result(tool: str, args: dict[str, Any], payload: Any, today: date) -> Any:
    """The envelope as a query result; anything else (an error, a non-list ``data``) is returned unchanged.
    A result without dates (campaign lists, budgets) is a snapshot as of now, so its window is ``today``: evidence
    drops undated rows that have no window (live 2026-11-21: budgets "unavailable")."""
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("data"), list):
        return payload
    rows = [_flat(r) for r in payload["data"] if isinstance(r, dict)]
    time_col = "date.hour" if any(HOUR_FIELD in r for r in rows) else "date.day" if any("date_start" in r for r in rows) else None
    keys = list(dict.fromkeys(k for r in rows for k in r))
    measures = [k for k in keys if not _is_id(k) and k != HOUR_FIELD
                and all(_num(r[k]) is not None for r in rows if r.get(k) not in (None, ""))
                and any(r.get(k) not in (None, "") for r in rows)]
    for r in rows:
        for m in measures:
            value = _num(r.get(m)) if r.get(m) not in (None, "") else None
            r[m] = value / 100 if value is not None and m in MINOR_UNIT_FIELDS else value
        if time_col == "date.hour":
            r[time_col] = f"{r.get('date_start')}T{str(r.get(HOUR_FIELD) or '')[:8]}"
        elif time_col:
            r[time_col] = r.get("date_start")
    hidden = {"date_start", "date_stop", HOUR_FIELD} if time_col else set()
    dims = [k for k in keys if k not in measures and k not in hidden and all(isinstance(r.get(k), (str, type(None))) for r in rows)]
    starts = sorted(str(r["date_start"]) for r in rows if r.get("date_start"))
    stops = sorted(str(r["date_stop"]) for r in rows if r.get("date_stop"))
    query_id = "live_" + hashlib.sha1((tool + json.dumps(args, sort_keys=True, default=str)).encode()).hexdigest()[:12]
    rest = {k: v for k, v in payload.items() if k != "data"}
    return {
        **rest,
        "query_id": query_id,
        "columns": dims + ([time_col] if time_col else []) + measures,
        "rows": rows,
        "row_count": len(rows),
        "provenance": {
            "query_id": query_id, "metric_ids": measures, "currency": "",
            "time_range": {"start": starts[0], "end": stops[-1]} if starts and stops else {"start": today.isoformat(), "end": today.isoformat()},
            "freshness": {"as_of": payload.get("as_of"), "source": f"live {payload.get('platform') or 'ads'} API"},
        },
        "note": _note(query_id, measures, time_col),
    }


def _note(query_id: str, measures: list[str], time_col: str | None) -> str:
    """How this result looks inside run_python: one evidence item per (row, measure), not these rows (live 2026-11-21:
    a script summed e['spend'] over evidence items, got 0 and the bot called a real alert cleared)."""
    example = measures[0] if measures else "spend"
    when = (" The hour is e['period_start'] (an ISO timestamp)." if time_col == "date.hour"
            else " The day is e['period_start']." if time_col else "")
    return (f"Live platform rows; budget fields are rupees (converted from paise). In run_python pass "
            f"query_ids=[{query_id!r}]: evidence then holds one item per row and measure — filter "
            f"e['metric_id'] == {example!r} and read e['value'];{when} Names are keys too (e['campaign_name']).")

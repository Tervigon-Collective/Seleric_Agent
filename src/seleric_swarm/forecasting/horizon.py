"""Turn forecast horizon slots into a concrete [start, end] window from as_of.

The time resolver only handles past windows; forecasts need a future window.
Uses the IST calendar day via ``services/time_range.as_of_date``.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any, Literal

from seleric_swarm.forecasting.types import HorizonWindow
from seleric_swarm.services.time_range import as_of_date

PeriodWord = Literal["next_n", "rest_of_month", "next_month", "this_quarter"]
HorizonUnit = Literal["day", "week", "month"]

_DEFAULT_DAYS = 14


def resolve_horizon(
    slots: Any | None,
    *,
    as_of: date | datetime | str,
    timezone: str = "Asia/Kolkata",
    max_horizon_days: int = 90,
    default_days: int = _DEFAULT_DAYS,
) -> HorizonWindow:
    """Map forecast slots (or defaults) to an inclusive future [start, end] window.

    Context ends at as_of − 1 (last complete day). Horizon starts at as_of
    (today / the asked anchor) for "next N days", or at the named future period.
    """
    anchor = _as_date(as_of, timezone)
    n, unit, period_word, until = _parse_slots(slots, default_days=default_days)

    if until is not None:
        start = anchor if until >= anchor else anchor
        end = until
        if end < start:
            end = start
        n_days = (end - start).days + 1
    elif period_word == "rest_of_month":
        start = anchor
        last = date(anchor.year, anchor.month, calendar.monthrange(anchor.year, anchor.month)[1])
        end = last
        n_days = (end - start).days + 1
        unit = "day"
    elif period_word == "next_month":
        if anchor.month == 12:
            start = date(anchor.year + 1, 1, 1)
        else:
            start = date(anchor.year, anchor.month + 1, 1)
        end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        n_days = (end - start).days + 1
        unit = "month"
    elif period_word == "this_quarter":
        q = (anchor.month - 1) // 3
        start = anchor
        end_month = q * 3 + 3
        end = date(anchor.year, end_month, calendar.monthrange(anchor.year, end_month)[1])
        n_days = (end - start).days + 1
        unit = "day"
    else:
        # next_n
        days = _to_days(n, unit)
        start = anchor
        end = start + timedelta(days=days - 1)
        n_days = days

    n_days = max(1, min(n_days, max_horizon_days))
    end = start + timedelta(days=n_days - 1)
    return HorizonWindow(
        start=start,
        end=end,
        n_days=n_days,
        period_word=period_word,
        unit=unit if unit in ("day", "week", "month") else "day",
    )


def _as_date(as_of: date | datetime | str, timezone: str) -> date:
    if isinstance(as_of, date) and not isinstance(as_of, datetime):
        return as_of
    if isinstance(as_of, datetime):
        return as_of.date()
    return as_of_date(str(as_of) if as_of else None, timezone)


def _parse_slots(
    slots: Any | None, *, default_days: int
) -> tuple[int, HorizonUnit, PeriodWord, date | None]:
    if slots is None:
        return default_days, "day", "next_n", None
    if isinstance(slots, dict):
        horizon = slots.get("horizon") or slots
    else:
        horizon = getattr(slots, "horizon", None) or slots

    def _get(obj: Any, key: str, default: Any = None) -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    period_word = str(_get(horizon, "period_word", "next_n") or "next_n")
    if period_word not in ("next_n", "rest_of_month", "next_month", "this_quarter"):
        period_word = "next_n"
    unit_raw = str(_get(horizon, "unit", "day") or "day")
    unit: HorizonUnit = unit_raw if unit_raw in ("day", "week", "month") else "day"
    n = int(_get(horizon, "n", default_days) or default_days)
    until_iso = _get(horizon, "until_iso", None)
    until: date | None = None
    if until_iso:
        try:
            until = date.fromisoformat(str(until_iso)[:10])
        except ValueError:
            until = None
    return max(1, n), unit, period_word, until  # type: ignore[return-value]


def _to_days(n: int, unit: HorizonUnit) -> int:
    if unit == "week":
        return n * 7
    if unit == "month":
        return n * 30
    return n

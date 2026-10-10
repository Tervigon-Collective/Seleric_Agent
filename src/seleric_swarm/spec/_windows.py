"""Window expression → calendar dates (stdlib date arithmetic only)."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from seleric_swarm.contracts.pipeline import WindowEnding, WindowExpr, WindowSlot, WindowSpec


def _last_complete_day(anchor: date) -> date:
    return anchor - timedelta(days=1)


def _sub_months(anchor: date, n: int) -> date:
    total = anchor.year * 12 + anchor.month - 1 - n
    year, month0 = divmod(total, 12)
    month = month0 + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def _month_span(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _end_day(anchor: date, ending: WindowEnding) -> date:
    if ending in ("today", "as_of"):
        return anchor
    return _last_complete_day(anchor)


def resolve_window_expr(
    expr: WindowExpr,
    *,
    as_of: date,
    event_start: date | None = None,
) -> tuple[date, date, str]:
    """Return (start, end, token). Raises ValueError when the expression is empty/invalid."""
    if expr.start_iso:
        start = date.fromisoformat(expr.start_iso[:10])
        end = date.fromisoformat((expr.end_iso or expr.start_iso)[:10])
        if end < start:
            start, end = end, start
        return start, end, "absolute"

    name = (expr.name or "").strip().lower()
    if name == "yesterday":
        day = as_of - timedelta(days=1)
        return day, day, "yesterday"
    if name == "today":
        return as_of, as_of, "today"
    if name == "this_week":
        start = as_of - timedelta(days=as_of.weekday())
        return start, as_of, "this_week"
    if name == "last_week":
        this_monday = as_of - timedelta(days=as_of.weekday())
        start = this_monday - timedelta(weeks=1)
        end = this_monday - timedelta(days=1)
        return start, end, "last_week"
    if name == "this_month":
        return as_of.replace(day=1), as_of, "this_month"
    if name == "last_month":
        prev = _sub_months(as_of, 1)
        start, end = _month_span(prev.year, prev.month)
        return start, end, "last_month"
    if name == "this_year":
        return as_of.replace(month=1, day=1), as_of, "this_year"
    if name == "last_year":
        year = as_of.year - 1
        return date(year, 1, 1), date(year, 12, 31), "last_year"

    unit = (expr.unit or "").strip().lower()
    n = int(expr.n or 0)
    if unit and n > 0:
        if expr.ending == "before_event":
            if event_start is None:
                raise ValueError("before_event baseline needs a resolved event window")
            end = event_start - timedelta(days=1)
            if unit == "day":
                return end - timedelta(days=n - 1), end, f"prior_{n}d"
            if unit == "week":
                return end - timedelta(weeks=n) + timedelta(days=1), end, f"prior_{n}w"
            if unit == "month":
                return _sub_months(end + timedelta(days=1), n), end, f"prior_{n}m"
            if unit == "quarter":
                return _sub_months(end + timedelta(days=1), n * 3), end, f"prior_{n}q"
            raise ValueError(f"unsupported baseline unit: {unit}")
        end = _end_day(as_of, expr.ending)
        if unit == "day":
            return end - timedelta(days=n - 1), end, f"last_{n}d"
        if unit == "week":
            return end - timedelta(weeks=n) + timedelta(days=1), end, f"last_{n}w"
        if unit == "month":
            return _sub_months(end + timedelta(days=1), n), end, f"last_{n}m"
        if unit == "quarter":
            return _sub_months(end + timedelta(days=1), n * 3), end, f"last_{n}q"
        if unit == "year":
            return _sub_months(end + timedelta(days=1), n * 12), end, f"last_{n}y"
        raise ValueError(f"unsupported window unit: {unit}")

    raise ValueError("empty window expression")


def resolve_window_slots(
    slots: tuple[WindowSlot, ...] | list[WindowSlot],
    *,
    as_of: date,
) -> tuple[WindowSpec, ...]:
    """Resolve draft window slots into ``WindowSpec``s (event first for before_event)."""
    ordered = sorted(
        slots or (),
        key=lambda slot: {"event": 0, "context": 1, "baseline": 2}.get(slot.role, 9),
    )
    event_start: date | None = None
    out: list[WindowSpec] = []
    for slot in ordered:
        try:
            start, end, token = resolve_window_expr(
                slot.expr, as_of=as_of, event_start=event_start
            )
        except ValueError:
            continue
        if slot.role == "event":
            event_start = start
        out.append(
            WindowSpec(
                role=slot.role,
                start=start,
                end=end,
                source_span=slot.source_span,
                token=token,
            )
        )
    role_order = {"event": 0, "baseline": 1, "context": 2}
    out.sort(key=lambda window: role_order.get(window.role, 9))
    return tuple(out)

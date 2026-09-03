from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seleric_swarm.contracts.lookup import TimeRangeV1


def as_of_date(as_of: str | None, timezone: str) -> date:
    if as_of:
        return date.fromisoformat(as_of[:10])
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except ZoneInfoNotFoundError:
        return datetime.now(UTC).date()


def resolve_time_range(time_range: TimeRangeV1, timezone: str, as_of: str | None) -> TimeRangeV1:
    anchor = as_of_date(as_of, timezone)
    if time_range.kind == "absolute" and time_range.start:
        day = time_range.start[:10]
        return TimeRangeV1(kind="absolute", start=day, end=time_range.end[:10] if time_range.end else day)
    if time_range.kind == "relative":
        token = time_range.relative_token or "yesterday"
        if token == "today":
            day = anchor.isoformat()
        elif token == "yesterday":
            day = (anchor - timedelta(days=1)).isoformat()
        else:
            day = (anchor - timedelta(days=1)).isoformat()
        return TimeRangeV1(kind="absolute", start=day, end=day, relative_token=token)
    if time_range.kind == "comparison":
        start = time_range.start
        end = time_range.end
        if time_range.relative_token == "yesterday_vs_as_of":
            start = (anchor - timedelta(days=1)).isoformat()
            end = anchor.isoformat()
        if not start or not end:
            raise ValueError("comparison time range requires two dates")
        return TimeRangeV1(kind="comparison", start=start[:10], end=end[:10], relative_token=time_range.relative_token)
    return time_range

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seleric_swarm.contracts.lookup import TimeRangeV1

_LAST_N_DAYS = re.compile(r"\blast\s+(\d+)\s+days?\b", re.IGNORECASE)
_ISO_DAY = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_COMPARISON_VERB = re.compile(r"\b(compare|versus|vs\.?|against|change|delta|over)\b", re.IGNORECASE)
# relative period → day offset for a two-point (point-vs-point) comparison
_RELATIVE_COMPARE = (
    (re.compile(r"\b(week[\s-]*over[\s-]*week|wow|this week\b.*\blast week|last week)\b", re.IGNORECASE), 7),
    (re.compile(r"\b(month[\s-]*over[\s-]*month|mom|this month\b.*\blast month|last month)\b", re.IGNORECASE), 30),
    (re.compile(r"\b(year[\s-]*over[\s-]*year|yoy|this year\b.*\blast year|last year)\b", re.IGNORECASE), 365),
)


def as_of_date(as_of: str | None, timezone: str) -> date:
    if as_of:
        return date.fromisoformat(as_of[:10])
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except ZoneInfoNotFoundError:
        return datetime.now(UTC).date()


def window_from_query(query: str, timezone: str, as_of: str | None) -> TimeRangeV1 | None:
    """Resolve an explicit window from the question: last-N, ISO dates, yesterday/today."""
    text = query or ""
    found = _LAST_N_DAYS.search(text)
    if found:
        n = max(1, min(int(found.group(1)), 90))
        end = as_of_date(as_of, timezone)
        start = end - timedelta(days=n - 1)
        return TimeRangeV1(
            kind="absolute",
            start=start.isoformat(),
            end=end.isoformat(),
            relative_token=f"last_{n}d",
        )
    dates = _ISO_DAY.findall(text)
    if len(dates) >= 2:
        return TimeRangeV1(kind="comparison", start=dates[0], end=dates[1], relative_token=None)
    if dates:
        return TimeRangeV1(kind="absolute", start=dates[0], end=dates[0], relative_token=None)
    # Relative period-over-period ("this week vs last week", "MoM change") resolves
    # to a two-point comparison anchored on as_of, so the mission runs instead of
    # failing with "comparison time range requires two dates".
    if _COMPARISON_VERB.search(text):
        for pattern, offset_days in _RELATIVE_COMPARE:
            if pattern.search(text):
                anchor = as_of_date(as_of, timezone)
                prior = anchor - timedelta(days=offset_days)
                return TimeRangeV1(
                    kind="comparison",
                    start=prior.isoformat(),
                    end=anchor.isoformat(),
                    relative_token=f"prior_{offset_days}d_vs_as_of",
                )
    lower = text.lower()
    anchor = as_of_date(as_of, timezone)
    if re.search(r"\byesterday\b", lower):
        day = (anchor - timedelta(days=1)).isoformat()
        return TimeRangeV1(kind="absolute", start=day, end=day, relative_token="yesterday")
    if re.search(r"\btoday\b", lower):
        day = anchor.isoformat()
        return TimeRangeV1(kind="absolute", start=day, end=day, relative_token="today")
    return None


def resolve_time_range(time_range: TimeRangeV1, timezone: str, as_of: str | None) -> TimeRangeV1:
    anchor = as_of_date(as_of, timezone)
    if time_range.kind == "absolute" and time_range.start:
        day = time_range.start[:10]
        return TimeRangeV1(kind="absolute", start=day, end=time_range.end[:10] if time_range.end else day)
    if time_range.kind == "relative":
        token = time_range.relative_token or "yesterday"
        last_n = re.fullmatch(r"last_(\d+)d", token or "")
        if last_n:
            n = max(1, min(int(last_n.group(1)), 90))
            start = anchor - timedelta(days=n - 1)
            return TimeRangeV1(
                kind="absolute",
                start=start.isoformat(),
                end=anchor.isoformat(),
                relative_token=token,
            )
        if token == "today":
            day = anchor.isoformat()
        elif token == "yesterday":
            day = (anchor - timedelta(days=1)).isoformat()
        else:
            day = (anchor - timedelta(days=1)).isoformat()
        return TimeRangeV1(kind="absolute", start=day, end=day, relative_token=token)
    if time_range.kind == "comparison":
        cmp_start: str | None = time_range.start
        cmp_end: str | None = time_range.end
        if time_range.relative_token == "yesterday_vs_as_of":
            cmp_start = (anchor - timedelta(days=1)).isoformat()
            cmp_end = anchor.isoformat()
        if not cmp_start or not cmp_end:
            raise ValueError(
                "comparison needs two dated periods — give explicit dates or a "
                "period-over-period phrase (e.g. 'this week vs last week')"
            )
        return TimeRangeV1(
            kind="comparison",
            start=cmp_start[:10],
            end=cmp_end[:10],
            relative_token=time_range.relative_token,
        )
    return time_range

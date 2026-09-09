from __future__ import annotations

import re
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seleric_swarm.contracts.lookup import TimeRangeV1

_LAST_N_DAYS = re.compile(r"\blast\s+(\d+)\s+days?\b", re.IGNORECASE)
_LAST_N_WEEKS = re.compile(r"\blast\s+(\d+)\s+weeks?\b", re.IGNORECASE)
_LAST_N_MONTHS = re.compile(r"\blast\s+(\d+)\s+months?\b", re.IGNORECASE)
_LAST_N_QTRS = re.compile(r"\blast\s+(?:(\d+)\s+)?quarters?\b", re.IGNORECASE)
_ISO_DAY = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_COMPARISON_VERB = re.compile(r"\b(compare|versus|vs\.?|against|change|delta|over)\b", re.IGNORECASE)
# relative period → day offset for a two-point (point-vs-point) comparison
_RELATIVE_COMPARE = (
    (re.compile(r"\b(week[\s-]*over[\s-]*week|wow|this week\b.*\blast week|last week)\b", re.IGNORECASE), 7),
    (re.compile(r"\b(month[\s-]*over[\s-]*month|mom|this month\b.*\blast month|last month)\b", re.IGNORECASE), 30),
    (re.compile(r"\b(year[\s-]*over[\s-]*year|yoy|this year\b.*\blast year|last year)\b", re.IGNORECASE), 365),
)


def _sub_months(anchor: date, n: int) -> date:
    """Subtract n calendar months from anchor — no 30-day approximation.

    Clamps the day to the last day of the target month when the anchor day
    does not exist there (e.g. 31 Jan → 31 Oct → 30 Sep for n=4).
    """
    total = anchor.year * 12 + anchor.month - 1 - n
    y, m = divmod(total, 12)
    m += 1
    max_day = monthrange(y, m)[1]
    return anchor.replace(year=y, month=m, day=min(anchor.day, max_day))


def as_of_date(as_of: str | None, timezone: str) -> date:
    if as_of:
        return date.fromisoformat(as_of[:10])
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except ZoneInfoNotFoundError:
        return datetime.now(UTC).date()


def window_from_query(query: str, timezone: str, as_of: str | None) -> TimeRangeV1 | None:
    """Resolve an explicit window from the question: last-N days/weeks/months/quarters, ISO dates, yesterday/today.

    Priority order (first match wins):
      1. last N days       → last_Nd  (capped at 90 to guard against typos)
      2. last N weeks      → last_Nw  (calendar weeks, no cap)
      3. last N months     → last_Nm  (calendar months, no cap)
      4. last N quarters   → last_Nq  (calendar quarters, no cap)
      5. two ISO dates     → comparison window
      6. one ISO date      → single-day absolute
      7. period-over-period phrases with comparison verb
      8. yesterday / today / this week / this month / this year
    """
    text = query or ""
    anchor = as_of_date(as_of, timezone)

    found = _LAST_N_DAYS.search(text)
    if found:
        n = max(1, min(int(found.group(1)), 90))
        start = anchor - timedelta(days=n - 1)
        return TimeRangeV1(
            kind="absolute",
            start=start.isoformat(),
            end=anchor.isoformat(),
            relative_token=f"last_{n}d",
        )

    found = _LAST_N_WEEKS.search(text)
    if found:
        n = int(found.group(1))
        start = anchor - timedelta(weeks=n)
        return TimeRangeV1(
            kind="absolute",
            start=start.isoformat(),
            end=anchor.isoformat(),
            relative_token=f"last_{n}w",
        )

    found = _LAST_N_MONTHS.search(text)
    if found:
        n = int(found.group(1))
        start = _sub_months(anchor, n)
        return TimeRangeV1(
            kind="absolute",
            start=start.isoformat(),
            end=anchor.isoformat(),
            relative_token=f"last_{n}m",
        )

    found = _LAST_N_QTRS.search(text)
    if found:
        # group(1) may be None when "last quarter" is used without an explicit N
        n = int(found.group(1)) if found.group(1) else 1
        start = _sub_months(anchor, n * 3)
        return TimeRangeV1(
            kind="absolute",
            start=start.isoformat(),
            end=anchor.isoformat(),
            relative_token=f"last_{n}q",
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
                prior = anchor - timedelta(days=offset_days)
                return TimeRangeV1(
                    kind="comparison",
                    start=prior.isoformat(),
                    end=anchor.isoformat(),
                    relative_token=f"prior_{offset_days}d_vs_as_of",
                )
    lower = text.lower()
    if re.search(r"\byesterday\b", lower):
        day = (anchor - timedelta(days=1)).isoformat()
        return TimeRangeV1(kind="absolute", start=day, end=day, relative_token="yesterday")
    if re.search(r"\btoday\b", lower):
        day = anchor.isoformat()
        return TimeRangeV1(kind="absolute", start=day, end=day, relative_token="today")
    if re.search(r"\bthis\s+month\b", lower):
        return TimeRangeV1(
            kind="absolute", start=anchor.replace(day=1).isoformat(), end=anchor.isoformat(),
            relative_token="this_month",
        )
    if re.search(r"\bthis\s+week\b", lower):
        start = anchor - timedelta(days=anchor.weekday())
        return TimeRangeV1(kind="absolute", start=start.isoformat(), end=anchor.isoformat(), relative_token="this_week")
    if re.search(r"\bthis\s+year\b", lower):
        return TimeRangeV1(
            kind="absolute", start=anchor.replace(month=1, day=1).isoformat(), end=anchor.isoformat(),
            relative_token="this_year",
        )
    return None


def resolve_time_range(time_range: TimeRangeV1, timezone: str, as_of: str | None) -> TimeRangeV1:
    anchor = as_of_date(as_of, timezone)
    if time_range.kind == "absolute" and time_range.start:
        day = time_range.start[:10]
        return TimeRangeV1(kind="absolute", start=day, end=time_range.end[:10] if time_range.end else day)
    if time_range.kind == "relative":
        token = time_range.relative_token
        if not token:
            # kind="relative" without a token is a classifier/schema anomaly,
            # not "no time phrase" (that case is kind="none") — the classify
            # prompt is responsible for always filling relative_token when it
            # picks kind="relative", so this must raise, not silently guess.
            raise ValueError("relative time_range requires a relative_token")
        last_n = re.fullmatch(r"last_(\d+)d", token)
        if last_n:
            n = max(1, min(int(last_n.group(1)), 90))
            start = anchor - timedelta(days=n - 1)
            return TimeRangeV1(
                kind="absolute",
                start=start.isoformat(),
                end=anchor.isoformat(),
                relative_token=token,
            )
        last_nw = re.fullmatch(r"last_(\d+)w", token)
        if last_nw:
            n = int(last_nw.group(1))
            start = anchor - timedelta(weeks=n)
            return TimeRangeV1(
                kind="absolute",
                start=start.isoformat(),
                end=anchor.isoformat(),
                relative_token=token,
            )
        last_nm = re.fullmatch(r"last_(\d+)m", token)
        if last_nm:
            n = int(last_nm.group(1))
            start = _sub_months(anchor, n)
            return TimeRangeV1(
                kind="absolute",
                start=start.isoformat(),
                end=anchor.isoformat(),
                relative_token=token,
            )
        last_nq = re.fullmatch(r"last_(\d+)q", token)
        if last_nq:
            n = int(last_nq.group(1))
            start = _sub_months(anchor, n * 3)
            return TimeRangeV1(
                kind="absolute",
                start=start.isoformat(),
                end=anchor.isoformat(),
                relative_token=token,
            )
        if token == "today":
            day = anchor.isoformat()
            return TimeRangeV1(kind="absolute", start=day, end=day, relative_token=token)
        if token == "yesterday":
            day = (anchor - timedelta(days=1)).isoformat()
            return TimeRangeV1(kind="absolute", start=day, end=day, relative_token=token)
        if token == "this_week":
            start = anchor - timedelta(days=anchor.weekday())
            return TimeRangeV1(kind="absolute", start=start.isoformat(), end=anchor.isoformat(), relative_token=token)
        if token == "this_month":
            return TimeRangeV1(
                kind="absolute", start=anchor.replace(day=1).isoformat(), end=anchor.isoformat(), relative_token=token
            )
        if token == "this_year":
            return TimeRangeV1(
                kind="absolute", start=anchor.replace(month=1, day=1).isoformat(), end=anchor.isoformat(),
                relative_token=token,
            )
        raise ValueError(f"unrecognized relative_token: {token!r}")
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

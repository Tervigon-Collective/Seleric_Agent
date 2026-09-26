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
# A single explicit range: "2026-06-01 to 2026-06-30" / "... through ..." /
# "... – ..." / "... - ...". The separator needs surrounding whitespace so it
# can't be swallowed into either date's own hyphens.
_ISO_RANGE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s+(?:to|through|-|–)\s+(20\d{2}-\d{2}-\d{2})", re.IGNORECASE
)
_COMPARISON_VERB = re.compile(r"\b(compare|versus|vs\.?|against|change|delta|over)\b", re.IGNORECASE)
_MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(sorted(_MONTH_NAMES, key=len, reverse=True)) + r")\s+(\d{4})\b", re.IGNORECASE
)
# relative period → whether this is a full calendar-period comparison
# (period A = the current period to date, period B = the matching prior
# period) rather than a single point 7/30/365 days back.
_RELATIVE_COMPARE = (
    (re.compile(r"\b(week[\s-]*over[\s-]*week|wow|this week\b.*\blast week|last week)\b", re.IGNORECASE), "week"),
    (re.compile(r"\b(month[\s-]*over[\s-]*month|mom|this month\b.*\blast month|last month)\b", re.IGNORECASE), "month"),
    (re.compile(r"\b(year[\s-]*over[\s-]*year|yoy|this year\b.*\blast year|last year)\b", re.IGNORECASE), "year"),
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


def _month_range(year: int, month: int) -> tuple[str, str]:
    """Full calendar-month span, 1st through last day."""
    last_day = monthrange(year, month)[1]
    return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()


_PERIOD_VS_PRIOR_TOKENS = {
    "week": "this_week_vs_last_week",
    "month": "this_month_vs_last_month",
    "year": "this_year_vs_last_year",
}


def _period_vs_prior(anchor: date, unit: str) -> tuple[date, date, date, date]:
    """Current period-to-date vs the matching prior period, both full spans.

    ``unit`` is "week" | "month" | "year". Returns (a_start, a_end, b_start,
    b_end) — period A is the current period so far, period B is the same
    span one period back (day-of-period clamped via ``_sub_months`` for
    month/year, so e.g. Jan 31 → prior period end is the last valid day).
    """
    if unit == "week":
        a_start = anchor - timedelta(days=anchor.weekday())
        return a_start, anchor, a_start - timedelta(weeks=1), anchor - timedelta(weeks=1)
    if unit == "month":
        prior = _sub_months(anchor, 1)
        return anchor.replace(day=1), anchor, prior.replace(day=1), prior
    if unit == "year":
        prior = _sub_months(anchor, 12)
        return anchor.replace(month=1, day=1), anchor, prior.replace(month=1, day=1), prior
    raise ValueError(f"unrecognized period unit: {unit!r}")


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
      1. last N days           → last_Nd  (capped at 90 to guard against typos)
      2. last N weeks          → last_Nw  (calendar weeks, no cap)
      3. last N months         → last_Nm  (calendar months, no cap)
      4. last N quarters       → last_Nq  (calendar quarters, no cap)
      5. two explicit ranges   → comparison, period A vs period B (each a
         real start–end span, e.g. "2026-06-01 to 2026-06-30 and 2026-08-01
         to 2026-08-31")
      6. one explicit range    → absolute window
      7. two named months      → comparison of the two full calendar months
         (e.g. "June 2026 vs August 2026")
      8. one named month       → absolute window, that full calendar month
      9. two loose ISO dates   → comparison, day A vs day B (single points)
      10. one ISO date         → single-day absolute
      11. period-over-period phrases with comparison verb → comparison of
          two full calendar periods (this week/month/year to date vs the
          matching prior period)
      12. yesterday / today / this week / this month / this year
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

    ranges = _ISO_RANGE.findall(text)
    if len(ranges) >= 2:
        (a_start, a_end), (b_start, b_end) = ranges[0], ranges[1]
        return TimeRangeV1(kind="comparison", start=a_start, end=a_end, start_b=b_start, end_b=b_end)
    if len(ranges) == 1:
        start, end = ranges[0]
        return TimeRangeV1(kind="absolute", start=start, end=end)

    months = _MONTH_YEAR.findall(text)
    if len(months) >= 2:
        (a_name, a_year), (b_name, b_year) = months[0], months[1]
        a_start, a_end = _month_range(int(a_year), _MONTH_NAMES[a_name.lower()])
        b_start, b_end = _month_range(int(b_year), _MONTH_NAMES[b_name.lower()])
        return TimeRangeV1(kind="comparison", start=a_start, end=a_end, start_b=b_start, end_b=b_end)
    if len(months) == 1:
        name, year = months[0]
        month_start, month_end = _month_range(int(year), _MONTH_NAMES[name.lower()])
        return TimeRangeV1(kind="absolute", start=month_start, end=month_end)

    dates = _ISO_DAY.findall(text)
    if len(dates) >= 2:
        return TimeRangeV1(kind="comparison", start=dates[0], end=dates[0], start_b=dates[1], end_b=dates[1])
    if dates:
        return TimeRangeV1(kind="absolute", start=dates[0], end=dates[0], relative_token=None)
    # Relative period-over-period ("this week vs last week", "MoM change") resolves
    # to a full-period comparison anchored on as_of, so the mission runs instead of
    # failing with "comparison time range requires two dates".
    if _COMPARISON_VERB.search(text):
        for pattern, unit in _RELATIVE_COMPARE:
            if not pattern.search(text):
                continue
            a_start, a_end, b_start, b_end = _period_vs_prior(anchor, unit)
            return TimeRangeV1(
                kind="comparison",
                start=a_start.isoformat(),
                end=a_end.isoformat(),
                start_b=b_start.isoformat(),
                end_b=b_end.isoformat(),
                relative_token=_PERIOD_VS_PRIOR_TOKENS[unit],
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
    # Bare "last month/week/year" (no N, no comparison verb) = the *previous
    # complete* calendar period, not the current one to date. Without this the
    # LLM resolved it itself and drifted — "last month" read as August in one
    # mission and September (this month) in another (live L1 vs L7).
    if re.search(r"\blast\s+month\b", lower):
        prev = _sub_months(anchor, 1)
        start, end = _month_range(prev.year, prev.month)
        return TimeRangeV1(kind="absolute", start=start, end=end, relative_token="last_month")
    if re.search(r"\blast\s+week\b", lower):
        this_monday = anchor - timedelta(days=anchor.weekday())
        start = this_monday - timedelta(weeks=1)
        end = this_monday - timedelta(days=1)
        return TimeRangeV1(kind="absolute", start=start.isoformat(), end=end.isoformat(), relative_token="last_week")
    if re.search(r"\blast\s+year\b", lower):
        y = anchor.year - 1
        return TimeRangeV1(kind="absolute", start=date(y, 1, 1).isoformat(), end=date(y, 12, 31).isoformat(), relative_token="last_year")
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
        if token == "last_month":
            prev = _sub_months(anchor, 1)
            start, end = _month_range(prev.year, prev.month)
            return TimeRangeV1(kind="absolute", start=start, end=end, relative_token=token)
        if token == "last_week":
            this_monday = anchor - timedelta(days=anchor.weekday())
            start = this_monday - timedelta(weeks=1)
            end = this_monday - timedelta(days=1)
            return TimeRangeV1(kind="absolute", start=start.isoformat(), end=end.isoformat(), relative_token=token)
        if token == "last_year":
            y = anchor.year - 1
            return TimeRangeV1(kind="absolute", start=date(y, 1, 1).isoformat(), end=date(y, 12, 31).isoformat(), relative_token=token)
        raise ValueError(f"unrecognized relative_token: {token!r}")
    if time_range.kind == "comparison":
        token = time_range.relative_token
        cmp_start: str | None = time_range.start
        cmp_end: str | None = time_range.end
        cmp_start_b: str | None = time_range.start_b
        cmp_end_b: str | None = time_range.end_b
        if token == "yesterday_vs_as_of":
            cmp_start = cmp_end = (anchor - timedelta(days=1)).isoformat()
            cmp_start_b = cmp_end_b = anchor.isoformat()
        elif token in _PERIOD_VS_PRIOR_TOKENS.values():
            unit = next(u for u, t in _PERIOD_VS_PRIOR_TOKENS.items() if t == token)
            a_start, a_end, b_start, b_end = _period_vs_prior(anchor, unit)
            cmp_start, cmp_end = a_start.isoformat(), a_end.isoformat()
            cmp_start_b, cmp_end_b = b_start.isoformat(), b_end.isoformat()
        if not cmp_start or not cmp_end:
            raise ValueError(
                "comparison needs two dated periods — give explicit start/end dates "
                "for both periods, or a period-over-period phrase "
                "(e.g. 'this week vs last week', 'June 2026 vs August 2026')"
            )
        result = TimeRangeV1(
            kind="comparison",
            start=cmp_start[:10],
            end=cmp_end[:10],
            relative_token=token,
        )
        if cmp_start_b and cmp_end_b:
            result.start_b = cmp_start_b[:10]
            result.end_b = cmp_end_b[:10]
        return result
    return time_range

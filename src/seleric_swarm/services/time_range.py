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
# A bare "last month" is NOT one of these. It is an explicit period the handlers
# below already resolve to that whole calendar month, and treating it as a
# period-over-period comparison whenever _COMPARISON_VERB appears anywhere in the
# sentence silently rewrites the window the user asked for: "for last month, how
# did this channel compare to the site average" is a channel-vs-site comparison,
# not a month-vs-month one, and it resolved to "September to date vs Aug 1-30",
# dropping Aug 31. So the period must be *bound* to the comparison — an idiom
# (month over month), both periods named, or a preposition joining them.
_VS = r"(?:vs\.?|versus|against|compared?\s+(?:to|with))\s+"
_RELATIVE_COMPARE = (
    (re.compile(rf"\b(week[\s-]*over[\s-]*week|wow|this week\b.*\blast week|{_VS}last week)\b", re.IGNORECASE), "week"),
    (re.compile(rf"\b(month[\s-]*over[\s-]*month|mom|this month\b.*\blast month|{_VS}last month)\b", re.IGNORECASE), "month"),
    (re.compile(rf"\b(year[\s-]*over[\s-]*year|yoy|this year\b.*\blast year|{_VS}last year)\b", re.IGNORECASE), "year"),
)


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")s?\b")

# Locator for "the question names a window here". Deliberately a *coarse*
# boundary finder only: each matched phrase is handed to ``_single_window``,
# which does the actual resolution, so this pattern can never drift from the
# single-window semantics. Ordered longest-alternative-first within each class
# so "2026-06-01 to 2026-06-30" wins over the bare day inside it.
_WINDOW_PHRASE = re.compile(
    r"last\s+\d+\s+(?:days?|weeks?|months?|quarters?)"
    r"|(?:" + "|".join(sorted(_MONTH_NAMES, key=len, reverse=True)) + r")\s+\d{4}"
    r"|20\d{2}-\d{2}-\d{2}(?:\s+(?:to|through|-|–)\s+20\d{2}-\d{2}-\d{2})?"
    r"|this\s+(?:week|month|year)"
    r"|last\s+(?:week|month|year)"
    r"|(?:today|yesterday)",
    re.IGNORECASE,
)


# Words that may join two windows named side by side in a comparison ("A and B", "A, B").
_MAX_JOINER_WORDS = 2


def _two_named_windows(text: str, anchor: date) -> TimeRangeV1 | None:
    """Two separately named windows joined by a comparison preposition.

    "last 3 days versus today" names two periods. The chain in
    ``_single_window`` returns on its first match, so the second was dropped and
    the mission silently answered a different question (live MS3-167d9f4838).
    This resolves the pair first and emits ``kind="comparison"``.

    Precision is the whole design. Two conditions, both required:

    - **A comparison preposition sits between the two phrases.** The same
      reasoning as ``_RELATIVE_COMPARE`` above: the second window must be *bound*
      to the comparison, not merely present in the sentence. "for last month, how
      did this channel compare to the site average" names one window and a
      non-temporal comparison; "change" and "over" in the looser
      ``_COMPARISON_VERB`` must not manufacture a period-over-period reading, so
      only ``_VS``-style joiners count here.
    - **The two resolve to different spans.** "last 7 days vs the last 7 days"
      names no comparison; falling through leaves the single-window answer.

    Fail-open throughout: anything not matching both conditions returns ``None``
    and the original behaviour is untouched.
    """
    matches = list(_WINDOW_PHRASE.finditer(text))
    if len(matches) < 2:
        return None
    first, second = matches[0], matches[1]
    between = text[first.end() : second.start()]
    # Bound by a "vs"-style joiner, or — in a question that asks to compare — named side by
    # side with only a short joiner between them ("compare … from yesterday and today": today
    # was dropped, so the same-hours comparison never ran, live 2026-10-09 MS3-0f473daefd).
    side_by_side = len(between.split()) <= _MAX_JOINER_WORDS and _COMPARISON_VERB.search(text) is not None
    if not (re.search(_VS, between, re.IGNORECASE) or side_by_side):
        return None

    period_a = _single_window(first.group(0), anchor)
    period_b = _single_window(second.group(0), anchor)
    if period_a is None or period_b is None:
        return None
    if not (period_a.start and period_a.end and period_b.start and period_b.end):
        return None
    if (period_a.start, period_a.end) == (period_b.start, period_b.end):
        return None

    def _token(period: TimeRangeV1) -> str:
        return (period.relative_token or "custom").replace("_", " ")

    return TimeRangeV1(
        kind="comparison",
        start=period_a.start,
        end=period_a.end,
        start_b=period_b.start,
        end_b=period_b.end,
        # The token is what `_resolved_window_line` gates on to tell the model
        # the dates at all, so it must be populated for the pair too.
        relative_token=f"{_token(period_a)} vs {_token(period_b)}",
    )


def _last_complete_day(anchor: date) -> str:
    """End of a trailing "last N days/weeks/months/quarters" window: yesterday.
    Today is still in progress, so "last 7 days" is the 7 complete days before
    it — the gateway's last_7d preset does the same. Ending on today made the
    diagnosis drop the partial day and analyse 6 days (live thread_e75c2615:
    "last 7 days" = 09-28..10-03), and weeks/months/quarters ran one day long."""
    return (anchor - timedelta(days=1)).isoformat()


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
    """Resolve the question's time window(s).

    A question that names **two** windows joined by a comparison preposition
    ("last 3 days versus today") resolves to ``kind="comparison"`` with both
    periods, so the second one is never silently dropped. Before 2026-10-06 the
    priority chain returned on its *first* match, so "the last 3 days versus
    today" resolved to ``2026-10-03..2026-10-05`` and ``today`` vanished: the
    model was told one window, fetched one window, and answered half the
    question as ``completed`` (live MS3-167d9f4838). Nothing downstream could
    catch it — ``RequiredScope`` had no slot for a second window, so
    ``check_scope_coverage`` had nothing to reconcile.

    Everything else defers to ``_single_window``, which owns the single-window
    priority chain unchanged.
    """
    text = query or ""
    anchor = as_of_date(as_of, timezone)
    if (comparison := _two_named_windows(text, anchor)) is not None:
        return comparison
    return _single_window(text, anchor)


def _single_window(text: str, anchor: date) -> TimeRangeV1 | None:
    """Resolve one explicitly named window. Priority order (first match wins):
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
    found = _LAST_N_DAYS.search(text)
    if found:
        n = max(1, min(int(found.group(1)), 90))
        start = anchor - timedelta(days=n)
        return TimeRangeV1(
            kind="absolute",
            start=start.isoformat(),
            end=_last_complete_day(anchor),
            relative_token=f"last_{n}d",
        )

    found = _LAST_N_WEEKS.search(text)
    if found:
        n = int(found.group(1))
        start = anchor - timedelta(weeks=n)
        return TimeRangeV1(
            kind="absolute",
            start=start.isoformat(),
            end=_last_complete_day(anchor),
            relative_token=f"last_{n}w",
        )

    found = _LAST_N_MONTHS.search(text)
    if found:
        n = int(found.group(1))
        start = _sub_months(anchor, n)
        return TimeRangeV1(
            kind="absolute",
            start=start.isoformat(),
            end=_last_complete_day(anchor),
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
            end=_last_complete_day(anchor),
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
    # A single named weekday ("on Monday", "last Monday") = its most recent
    # COMPLETED occurrence, strictly before as_of: today is still in progress.
    # Left to the model before, which once dated "Monday" as a Sunday (live
    # 2026-10-04) and diagnosed an incomplete day. Two names = a comparison,
    # not handled here.
    named = {m.group(1) for m in _WEEKDAY.finditer(lower)}
    if len(named) == 1:
        target = _WEEKDAYS.index(named.pop())
        back = (anchor.weekday() - target) % 7 or 7
        day = (anchor - timedelta(days=back)).isoformat()
        return TimeRangeV1(kind="absolute", start=day, end=day, relative_token=f"last_{_WEEKDAYS[target]}")
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
            start = anchor - timedelta(days=n)
            return TimeRangeV1(
                kind="absolute",
                start=start.isoformat(),
                end=_last_complete_day(anchor),
                relative_token=token,
            )
        last_nw = re.fullmatch(r"last_(\d+)w", token)
        if last_nw:
            n = int(last_nw.group(1))
            start = anchor - timedelta(weeks=n)
            return TimeRangeV1(
                kind="absolute",
                start=start.isoformat(),
                end=_last_complete_day(anchor),
                relative_token=token,
            )
        last_nm = re.fullmatch(r"last_(\d+)m", token)
        if last_nm:
            n = int(last_nm.group(1))
            start = _sub_months(anchor, n)
            return TimeRangeV1(
                kind="absolute",
                start=start.isoformat(),
                end=_last_complete_day(anchor),
                relative_token=token,
            )
        last_nq = re.fullmatch(r"last_(\d+)q", token)
        if last_nq:
            n = int(last_nq.group(1))
            start = _sub_months(anchor, n * 3)
            return TimeRangeV1(
                kind="absolute",
                start=start.isoformat(),
                end=_last_complete_day(anchor),
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

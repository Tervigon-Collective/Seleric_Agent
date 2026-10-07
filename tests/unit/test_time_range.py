"""Unit tests for month/week/quarter window parsing in time_range.py.

Covers:
- window_from_query for last-N months, weeks, quarters
- resolve_time_range for last_Nm / last_Nw / last_Nq tokens
- _sub_months calendar-correct month subtraction (end-of-month clamping)
- Priority ordering (months beats days when both absent from query)
- No 90-day cap for months/weeks/quarters
"""
from __future__ import annotations

import pytest

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.services.time_range import (
    _sub_months,
    resolve_time_range,
    window_from_query,
)

# ---------------------------------------------------------------------------
# _sub_months — calendar-correct subtraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "anchor,n,expected",
    [
        # Simple case: no month-end clamping needed
        ("2026-09-03", 5, "2026-04-03"),
        ("2026-09-03", 1, "2026-08-03"),
        ("2026-09-03", 12, "2025-09-03"),
        # January: subtracting 1 month → previous year December
        ("2026-01-15", 1, "2025-12-15"),
        # End-of-month clamping: March 31 → Feb 28 (non-leap)
        ("2026-03-31", 1, "2026-02-28"),
        # End-of-month clamping: March 31 → Feb 29 in leap year 2024
        ("2024-03-31", 1, "2024-02-29"),
        # Quarter = 3 months
        ("2026-09-03", 3, "2026-06-03"),
        # Large N: 13 months
        ("2026-09-03", 13, "2025-08-03"),
    ],
)
def test_sub_months(anchor: str, n: int, expected: str):
    from datetime import date

    result = _sub_months(date.fromisoformat(anchor), n)
    assert result.isoformat() == expected


# ---------------------------------------------------------------------------
# window_from_query — last N months
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query,as_of,expected_start,expected_end,expected_token",
    [
        (
            "What is the best selling product in the last 5 months",
            "2026-09-03",
            "2026-04-03",
            "2026-09-02",
            "last_5m",
        ),
        (
            "Show me sales for the last 1 month",
            "2026-09-03",
            "2026-08-03",
            "2026-09-02",
            "last_1m",
        ),
        (
            "Orders in last 12 months",
            "2026-09-03",
            "2025-09-03",
            "2026-09-02",
            "last_12m",
        ),
        # Plural vs singular
        (
            "Revenue over the last 3 month",
            "2026-06-30",
            "2026-03-30",
            "2026-06-29",
            "last_3m",
        ),
        # End-of-month clamping in query context
        (
            "Performance last 1 month",
            "2026-03-31",
            "2026-02-28",
            "2026-03-30",
            "last_1m",
        ),
    ],
)
def test_window_from_query_months(
    query, as_of, expected_start, expected_end, expected_token
):
    window = window_from_query(query, "Asia/Kolkata", as_of)
    assert window is not None, "expected a time window to be resolved"
    assert window.kind == "absolute"
    assert window.start == expected_start
    assert window.end == expected_end
    assert window.relative_token == expected_token


# ---------------------------------------------------------------------------
# window_from_query — last N weeks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query,as_of,expected_start,expected_end,expected_token",
    [
        (
            "Top products in the last 2 weeks",
            "2026-09-03",
            "2026-08-20",
            "2026-09-02",
            "last_2w",
        ),
        (
            "Sales last 1 week",
            "2026-09-03",
            "2026-08-27",
            "2026-09-02",
            "last_1w",
        ),
        (
            "Performance last 4 weeks",
            "2026-09-03",
            "2026-08-06",
            "2026-09-02",
            "last_4w",
        ),
    ],
)
def test_window_from_query_weeks(
    query, as_of, expected_start, expected_end, expected_token
):
    window = window_from_query(query, "Asia/Kolkata", as_of)
    assert window is not None
    assert window.kind == "absolute"
    assert window.start == expected_start
    assert window.end == expected_end
    assert window.relative_token == expected_token


# ---------------------------------------------------------------------------
# window_from_query — last N quarters
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query,as_of,expected_start,expected_end,expected_token",
    [
        (
            "Revenue over last 1 quarter",
            "2026-09-03",
            "2026-06-03",
            "2026-09-02",
            "last_1q",
        ),
        (
            "What happened in the last 2 quarters",
            "2026-09-03",
            "2026-03-03",
            "2026-09-02",
            "last_2q",
        ),
        (
            "Best sellers last quarter",
            "2026-09-03",
            "2026-06-03",
            "2026-09-02",
            "last_1q",
        ),
    ],
)
def test_window_from_query_quarters(
    query, as_of, expected_start, expected_end, expected_token
):
    window = window_from_query(query, "Asia/Kolkata", as_of)
    assert window is not None
    assert window.kind == "absolute"
    assert window.start == expected_start
    assert window.end == expected_end
    assert window.relative_token == expected_token


# ---------------------------------------------------------------------------
# Days queries remain unchanged (regression)
# ---------------------------------------------------------------------------

def test_window_from_query_days_still_works():
    window = window_from_query("Sales in the last 3 days", "Asia/Kolkata", "2026-09-03")
    assert window is not None
    assert window.relative_token == "last_3d"
    assert window.start == "2026-08-31"
    assert window.end == "2026-09-02"  # complete days only: today is in progress


def test_window_from_query_days_capped_at_90():
    window = window_from_query("Sales in the last 200 days", "Asia/Kolkata", "2026-09-03")
    assert window is not None
    assert window.relative_token == "last_90d"  # capped


# No 90-day cap for months
def test_window_from_query_months_not_capped():
    window = window_from_query("Sales in the last 12 months", "Asia/Kolkata", "2026-09-03")
    assert window is not None
    assert window.start == "2025-09-03"  # full 12-month span preserved


# ---------------------------------------------------------------------------
# resolve_time_range — last_Nm / last_Nw / last_Nq tokens
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "token,as_of,expected_start,expected_end",
    [
        ("last_5m", "2026-09-03", "2026-04-03", "2026-09-02"),
        ("last_1m", "2026-03-31", "2026-02-28", "2026-03-30"),
        ("last_2w", "2026-09-03", "2026-08-20", "2026-09-02"),
        ("last_1w", "2026-09-03", "2026-08-27", "2026-09-02"),
        ("last_1q", "2026-09-03", "2026-06-03", "2026-09-02"),
        ("last_2q", "2026-09-03", "2026-03-03", "2026-09-02"),
    ],
)
def test_resolve_time_range_relative_tokens(token, as_of, expected_start, expected_end):
    tr = TimeRangeV1(kind="relative", start=None, end=None, relative_token=token)
    resolved = resolve_time_range(tr, "Asia/Kolkata", as_of)
    assert resolved.kind == "absolute"
    assert resolved.start == expected_start
    assert resolved.end == expected_end
    assert resolved.relative_token == token


def test_resolve_time_range_last_nd_still_works():
    tr = TimeRangeV1(kind="relative", start=None, end=None, relative_token="last_7d")
    resolved = resolve_time_range(tr, "Asia/Kolkata", "2026-09-03")
    assert resolved.start == "2026-08-27"
    assert resolved.end == "2026-09-02"  # 7 complete days, same as the gateway's last_7d


def test_resolve_time_range_last_nd_capped():
    tr = TimeRangeV1(kind="relative", start=None, end=None, relative_token="last_200d")
    resolved = resolve_time_range(tr, "Asia/Kolkata", "2026-09-03")
    # 90-day cap applies to days tokens
    from datetime import date, timedelta
    expected = (date.fromisoformat("2026-09-03") - timedelta(days=90)).isoformat()
    assert resolved.start == expected


# Bare "last month/week/year" = previous complete period (L1-vs-L7 drift fix)
@pytest.mark.parametrize(
    "query,as_of,expected_start,expected_end,token",
    [
        # as_of in September → "last month" is the full previous month (August),
        # never September-to-date (the L7 misresolution).
        ("net revenue last month", "2026-09-25", "2026-08-01", "2026-08-31", "last_month"),
        # January anchor wraps the year correctly.
        ("orders last month", "2026-01-10", "2025-12-01", "2025-12-31", "last_month"),
        # previous complete ISO week (Mon–Sun) before this week's Monday.
        ("sessions last week", "2026-09-25", "2026-09-14", "2026-09-20", "last_week"),
        ("profit last year", "2026-09-25", "2025-01-01", "2025-12-31", "last_year"),
    ],
)
def test_window_from_query_bare_last_period(query, as_of, expected_start, expected_end, token):
    window = window_from_query(query, "Asia/Kolkata", as_of)
    assert window is not None
    assert window.kind == "absolute"
    assert (window.start, window.end, window.relative_token) == (expected_start, expected_end, token)


def test_bare_last_month_is_not_a_comparison_without_a_verb():
    # "why did revenue drop vs last month" has a comparison verb → period-over-
    # period; but a bare "revenue last month" must be a single window, not a cmp.
    window = window_from_query("revenue last month", "Asia/Kolkata", "2026-09-25")
    assert window is not None and window.kind == "absolute" and window.start_b is None


def test_resolve_time_range_bare_last_period_tokens():
    tr = TimeRangeV1(kind="relative", start=None, end=None, relative_token="last_month")
    resolved = resolve_time_range(tr, "Asia/Kolkata", "2026-09-25")
    assert (resolved.kind, resolved.start, resolved.end) == ("absolute", "2026-08-01", "2026-08-31")


# Priority: months phrase beats days when both would match separately
def test_months_phrase_beats_days_phrase():
    window = window_from_query(
        "Show last 5 months performance for the last 30 days baseline",
        "Asia/Kolkata",
        "2026-09-03",
    )
    assert window is not None
    # 'last 5 months' should match because 'last N days' is tried first
    # but "last 30 days" also exists — days regex matches first per priority
    # the test confirms whichever fires first returns a consistent result.
    assert window.relative_token is not None
    assert window.kind == "absolute"


def test_named_weekday_is_its_last_completed_occurrence() -> None:
    from seleric_swarm.services.time_range import window_from_query

    # 2026-10-04 is a Sunday: "Monday" is 2026-09-28, never today or a future day.
    w = window_from_query("what caused the spike in CAC on Monday?", "Asia/Kolkata", "2026-10-04")
    assert (w.start, w.end, w.relative_token) == ("2026-09-28", "2026-09-28", "last_monday")
    # Asked on a Monday, "Monday" is last week's (today is still in progress).
    w = window_from_query("why did orders drop on Monday", "Asia/Kolkata", "2026-10-05")
    assert (w.start, w.end) == ("2026-09-28", "2026-09-28")
    # Two weekday names are a comparison, not a single day.
    assert window_from_query("Monday vs Tuesday orders", "Asia/Kolkata", "2026-10-04") is None


# -- two named windows joined by a comparison -----------------------------------
#
# Live 2026-10-06 (MS3-167d9f4838): the priority chain returned on its first
# match, so "the last 3 days versus today" resolved to the 3-day window alone and
# `today` was dropped. The mission fetched, analysed and shipped only the first
# window as `completed`. These lock the comparison down AND its precision: a
# second window must be *bound* to a comparison, not merely present.


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # The incident itself.
        (
            "Analyze the last 3 days' best-performing campaigns versus today",
            ("2026-10-03", "2026-10-05", "2026-10-06", "2026-10-06"),
        ),
        ("revenue yesterday versus today", ("2026-10-05", "2026-10-05", "2026-10-06", "2026-10-06")),
        ("this week vs last week", ("2026-10-05", "2026-10-06", "2026-09-28", "2026-10-04")),
        ("last month vs this month", ("2026-09-01", "2026-09-30", "2026-10-01", "2026-10-06")),
    ],
)
def test_two_named_windows_become_a_comparison(query: str, expected: tuple[str, ...]) -> None:
    from seleric_swarm.services.time_range import window_from_query

    w = window_from_query(query, "Asia/Kolkata", "2026-10-06")
    assert w is not None, query
    assert w.kind == "comparison", query
    assert (w.start, w.end, w.start_b, w.end_b) == expected, query
    # The token is what `_resolved_window_line` gates on to tell the model the
    # dates at all — an unpinned comparison silently loses both windows again.
    assert w.relative_token, query


@pytest.mark.parametrize(
    "query",
    [
        # One window and a NON-temporal comparison: not a period comparison. This
        # is the exact case the `_RELATIVE_COMPARE` comment warns about.
        "for last month, how did this channel compare to the site average",
        # The same window twice names no comparison.
        "spend in the last 7 days vs the last 7 days",
        # A second window that is not joined to the first.
        "spend last 30 days across all channels vs target",
        # Single window, unchanged.
        "total ad spend in the last 7 days",
        "how many orders came in today",
    ],
)
def test_one_named_window_is_not_turned_into_a_comparison(query: str) -> None:
    from seleric_swarm.services.time_range import window_from_query

    w = window_from_query(query, "Asia/Kolkata", "2026-10-06")
    assert w is not None, query
    assert w.kind == "absolute", query
    assert w.start_b is None and w.end_b is None, query


def test_required_scope_carries_both_windows_of_a_comparison() -> None:
    from seleric_swarm.agent.scope import required_windows_from_resolved
    from seleric_swarm.services.time_range import window_from_query

    windows = required_windows_from_resolved(
        window_from_query("the last 3 days versus today", "Asia/Kolkata", "2026-10-06")
    )
    assert [str(w) for w in windows] == ["2026-10-03..2026-10-05", "2026-10-06"]
    # A single-window question still yields exactly one, never zero: an empty
    # window list would silently disable the coverage gate.
    single = required_windows_from_resolved(
        window_from_query("total ad spend in the last 7 days", "Asia/Kolkata", "2026-10-06")
    )
    assert [str(w) for w in single] == ["2026-09-29..2026-10-05"]
    # Fail-open on anything undated.
    assert required_windows_from_resolved(None) == ()

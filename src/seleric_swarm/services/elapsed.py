"""Today-in-progress facts shared by ``query_metrics`` and the answer validator.

The mission's ``as_of`` is midnight of the mission day (``runner._as_of_datetime``),
so how far into today the data reaches comes from the wall clock in the mission
timezone. Live 2026-10-06 (MS3-4c6633d347) and 2026-10-07 (MS3-4f7be7ba30) the
agent compared a 3-day total with today's first 6-18 hours and reported "spend
down 73%" / "orders down 90%" while today was in fact ahead of the same hours of
the reference days.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime, timedelta, tzinfo

# Evidence dimension carried by a same-elapsed-hours value ("12:00" = hours 00-11 of each day).
ELAPSED_KEY = "elapsed_through"


def now_in(tz: tzinfo | None) -> datetime:
    """Wall-clock now in the mission timezone (patched in tests)."""
    return datetime.now(tz)


def in_progress_day(as_of: datetime) -> date | None:
    """The mission day while it is still running (it is today), else None."""
    return as_of.date() if now_in(as_of.tzinfo).date() == as_of.date() else None


# The hour the user cuts a same-hours comparison at ("till 11 am" = 11), for this mission.
_STATED_CUTOFF: ContextVar[int | None] = ContextVar("elapsed_stated_cutoff", default=None)


def state_cutoff_hour(hour: int | None) -> None:
    """Set (or clear) the hour the question cuts the day at; set once per mission."""
    _STATED_CUTOFF.set(hour if hour is not None and 0 < hour <= 24 else None)


def completed_hours(as_of: datetime) -> int:
    """Whole hours of the mission day already elapsed (0-23), never past the hour the
    question named: "till 11 am" asked at 15:00 compares 00:00-11:00, not 00:00-15:00."""
    hour = now_in(as_of.tzinfo).hour
    stated = _STATED_CUTOFF.get()
    return min(hour, stated) if stated is not None else hour


def covers_in_progress_day(start: date, end: date, as_of: datetime) -> bool:
    """True when the window [start, end] includes today while today is still running."""
    today = in_progress_day(as_of)
    return today is not None and start <= today <= end


def same_span(earlier: tuple[date, date], later: tuple[date, date], as_of: datetime) -> tuple[date, date] | None:
    """The part of ``earlier`` a period to date compares with, or None when the
    windows compare as they are.

    When ``later`` runs to today over more than one day (this week, this month, the
    last N days with today) and ``earlier`` is at least as long, the like-for-like
    comparison is the same span: ``earlier`` cut to as many days as ``later`` has
    run, its last day counted to the hour today has reached. A single running day
    (today against the days before) compares per day over the same hours instead."""
    later_days = (later[1] - later[0]).days + 1
    if not covers_in_progress_day(later[0], later[1], as_of) or later_days < 2:
        return None
    if (earlier[1] - earlier[0]).days + 1 < later_days or earlier[1] >= later[0]:
        return None
    return earlier[0], earlier[0] + timedelta(days=later_days - 1)

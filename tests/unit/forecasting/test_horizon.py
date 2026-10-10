"""Horizon slot → [start, end] window."""

from __future__ import annotations

from datetime import date

from seleric_swarm.agent.understand import ForecastHorizonSlot, ForecastSlots
from seleric_swarm.forecasting.horizon import resolve_horizon


def test_default_next_14_days():
    w = resolve_horizon(None, as_of=date(2026, 10, 1), max_horizon_days=90)
    assert w.start == date(2026, 10, 1)
    assert w.end == date(2026, 10, 14)
    assert w.n_days == 14
    assert w.period_word == "next_n"


def test_clamped_to_max_horizon():
    slots = ForecastSlots(horizon=ForecastHorizonSlot(n=200, unit="day"))
    w = resolve_horizon(slots, as_of=date(2026, 10, 1), max_horizon_days=90)
    assert w.n_days == 90
    assert w.end == date(2026, 10, 1) + __import__("datetime").timedelta(days=89)


def test_rest_of_month():
    slots = ForecastSlots(horizon=ForecastHorizonSlot(period_word="rest_of_month"))
    w = resolve_horizon(slots, as_of=date(2026, 10, 20))
    assert w.start == date(2026, 10, 20)
    assert w.end == date(2026, 10, 31)
    assert w.n_days == 12


def test_next_month():
    slots = ForecastSlots(horizon=ForecastHorizonSlot(period_word="next_month"))
    w = resolve_horizon(slots, as_of=date(2026, 10, 9))
    assert w.start == date(2026, 11, 1)
    assert w.end == date(2026, 11, 30)
    assert w.unit == "month"


def test_weeks_to_days():
    slots = ForecastSlots(horizon=ForecastHorizonSlot(n=2, unit="week"))
    w = resolve_horizon(slots, as_of=date(2026, 10, 1))
    assert w.n_days == 14

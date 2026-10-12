"""AG-08: infer absolute windows like 1-11th October when the draft omits them."""

from datetime import date

from seleric_swarm.spec._windows import infer_absolute_window


def test_infer_1_to_11_october() -> None:
    w = infer_absolute_window(
        "I NEED Full Funnel Diagnostic (1-11th October). Drop Off Analysis.",
        date(2026, 10, 12),
    )
    assert w is not None
    assert w.start == date(2026, 10, 1)
    assert w.end == date(2026, 10, 11)
    assert w.role == "event"


def test_infer_with_to_separator() -> None:
    w = infer_absolute_window("sessions from 1 to 11 October", date(2026, 10, 12))
    assert w is not None
    assert (w.start, w.end) == (date(2026, 10, 1), date(2026, 10, 11))


def test_infer_none_without_month() -> None:
    assert infer_absolute_window("how did we do last week?", date(2026, 10, 12)) is None


def test_infer_month_of_october_mtd() -> None:
    w = infer_absolute_window(
        "product sales analysis for the month of October",
        date(2026, 10, 12),
    )
    assert w is not None
    assert (w.start, w.end) == (date(2026, 10, 1), date(2026, 10, 12))


def test_infer_named_month_full_when_past() -> None:
    w = infer_absolute_window("sales in September", date(2026, 10, 12))
    assert w is not None
    assert (w.start, w.end) == (date(2026, 9, 1), date(2026, 9, 30))

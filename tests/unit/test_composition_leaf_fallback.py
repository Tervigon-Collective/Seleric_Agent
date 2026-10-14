"""Leaf metrics without composition fall back to a parent that starts from them."""

from __future__ import annotations

from seleric_swarm.toolsets.composition import _parent_for_leaf


def test_gross_sales_falls_back_to_net_sales() -> None:
    definitions = {
        "gross_sales": {"display_name": "Gross sales", "formula": {}},
        "net_sales": {
            "display_name": "Net sales",
            "formula": {
                "composition": [
                    {"metric": "gross_sales", "sign": 1},
                    {"metric": "discounts", "sign": -1},
                    {"metric": "net_sales_deductions", "sign": -1},
                ]
            },
        },
        "total_sales": {
            "display_name": "Total sales",
            "formula": {
                "composition": [
                    {"metric": "paid_sales", "sign": 1},
                    {"metric": "discounts_incl_tax", "sign": 1},
                ]
            },
        },
    }
    assert _parent_for_leaf("gross_sales", definitions) == "net_sales"


def test_leaf_with_no_parent_returns_none() -> None:
    definitions = {
        "discounts": {"formula": {}},
        "net_sales": {
            "formula": {
                "composition": [
                    {"metric": "gross_sales", "sign": 1},
                    {"metric": "discounts", "sign": -1},
                ]
            }
        },
    }
    # discounts is a term but not the first +1 line — no parent fallback.
    assert _parent_for_leaf("discounts", definitions) is None


def _ctx_at(as_of):
    from types import SimpleNamespace

    return SimpleNamespace(deps=SimpleNamespace(as_of=as_of))


def test_a_waterfall_asked_for_only_today_shows_the_last_complete_day() -> None:
    """Live 2026-10-09: "net profit waterfall" with no period broke down today, a fraction of a day."""
    from datetime import UTC, date, datetime

    from seleric_swarm.toolsets import composition

    as_of = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)
    a, b, notes = composition._window(_ctx_at(as_of), None, None)  # type: ignore[arg-type]
    assert (a, b) == (date(2026, 10, 13), date(2026, 10, 13)) and not notes  # no period: yesterday, nothing to explain
    a, b, notes = composition._window(_ctx_at(as_of), as_of, as_of)  # type: ignore[arg-type]
    assert (a, b) == (date(2026, 10, 13), date(2026, 10, 13))
    assert notes and "still in progress" in notes[0]  # an explicit "today" is replaced, and says so


def test_a_named_complete_period_is_kept_as_asked() -> None:
    from datetime import UTC, date, datetime

    from seleric_swarm.toolsets import composition

    as_of = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)
    a, b, notes = composition._window(
        _ctx_at(as_of), datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC)  # type: ignore[arg-type]
    )
    assert (a, b) == (date(2026, 9, 1), date(2026, 9, 30)) and not notes

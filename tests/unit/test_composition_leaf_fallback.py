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

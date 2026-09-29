"""Deterministic prose audits over final_response.

Both cases here are live 2026-09-29 answers that the response contract forbade
in plain words and the model produced anyway.
"""

from __future__ import annotations

import pytest

from seleric_swarm.agent.validation.answer_audit import leaked_metric_ids, total_mismatch

_TABLE = """
| Period | Payment amount (INR) |
| --- | ---: |
| 2026-06 (PARTIAL) | 1,318,894.20 |
| 2026-07 | 3,200,573.30 |
| 2026-08 | 3,518,883.77 |
| 2026-09 (PARTIAL) | 3,904,658.70 |
"""

_LAKH_TABLE = """
| Month | Net Revenue |
| --- | ---: |
| Jun | 10,69,476 |
| Jul | 44,98,924 |
| Aug | 34,14,901 |
| Sep | 16,07,525 |
"""


def test_flags_a_total_that_contradicts_the_printed_table():
    """Live: led with INR 9,146,009 over rows summing to 11,943,009.97."""
    reason = total_mismatch(f"Total collected: INR 9,146,009 (sum of last 3 months).\n{_TABLE}")
    assert reason is not None
    assert "9,146,009" in reason and "11,943,009.97" in reason


def test_accepts_a_correct_total():
    assert total_mismatch(f"Total collected: INR 11,943,010 (sum of last 3 months).\n{_TABLE}") is None


def test_accepts_a_subset_total_excluding_partial_periods():
    """"Total excluding partial months" is a legitimate answer, not a mismatch."""
    assert total_mismatch(f"Total for full months only: 6,719,457.07.\n{_TABLE}") is None


def test_flags_an_abbreviated_total_off_by_one_scale_step():
    """Live: led with Rs 10.59 L over a table summing to 105.91 L."""
    assert total_mismatch(f"Attributed Net Revenue totalled Rs 10.59 L.\n{_LAKH_TABLE}") is not None


def test_accepts_a_correct_abbreviated_total():
    assert total_mismatch(f"Attributed Net Revenue totalled Rs 105.91 L.\n{_LAKH_TABLE}") is None


def test_period_counts_on_the_total_line_are_not_read_as_the_total():
    """"sum of last 3 months" must not make 3 the asserted total."""
    assert total_mismatch(f"Total: 11,943,010 (sum of last 3 months).\n{_TABLE}") is None


@pytest.mark.parametrize(
    "text",
    [
        "Net sales totalled 1,234 last month.",  # no table to reconcile against
        f"Here is the monthly breakdown.\n{_TABLE}",  # no total claimed
        "",
    ],
)
def test_no_false_positive_without_both_a_table_and_a_total(text):
    assert total_mismatch(text) is None


def test_detects_a_leaked_metric_id():
    """Live: "...use net_sales_all_channels when you need P&L net sales"."""
    text = "Use net_sales_all_channels when you need P&L net sales."
    assert leaked_metric_ids(text, {"net_sales_all_channels", "payment_amount_signed"}) == [
        "net_sales_all_channels"
    ]


def test_plain_business_prose_leaks_nothing():
    text = "Net sales across all channels rose 12% month over month."
    assert leaked_metric_ids(text, {"net_sales_all_channels", "payment_amount_signed"}) == []


def test_single_word_ids_are_not_matched_against_ordinary_vocabulary():
    """A bare id like "revenue" would collide with business language."""
    assert leaked_metric_ids("Revenue grew this month.", {"revenue", "roas"}) == []

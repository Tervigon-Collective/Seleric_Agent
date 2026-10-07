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


_TABLE_B = """
| Period | Payment amount (INR) |
| --- | ---: |
| 2026-06 (PARTIAL) | 1,318,894.20 |
| 2026-07 | 3,200,573.30 |
| 2026-08 | 3,518,883.77 |
| 2026-09 (PARTIAL) | 3,907,157.70 |
"""


@pytest.mark.parametrize("word", ["totaled", "totalled", "totals", "summed", "combined"])
def test_total_claims_are_recognised_in_either_spelling(word):
    """Live 2026-09-30: an answer wrote "totaled" (one l) and an earlier
    pattern listing only "totalled" let a wrong total through unchecked."""
    assert total_mismatch(f"Sales {word} INR 8,054,508 here.\n{_TABLE_B}") is not None


def test_tolerance_is_precision_based_not_proportional():
    """Live 2026-09-30: 8,054,508 sat within 0.5% of the 8,038,351.27 subset
    (a 40,191 window), so a proportional tolerance accepted a wrong total.
    A figure written to the rupee may only hide half a rupee."""
    assert total_mismatch(f"Sales totaled INR 8,054,508.\n{_TABLE_B}") is not None
    # The genuine subset, stated exactly, still reconciles.
    assert total_mismatch(f"Sales totaled INR 8,038,351.27.\n{_TABLE_B}") is None


def test_a_rounded_magnitude_in_prose_still_reconciles():
    """"~3.91M" is a legitimate rounding of 3,907,157.70 — half of its last
    represented unit is 5,000, which covers the 2,842 gap."""
    assert total_mismatch(f"Receipts rose to ~3.91M INR in the summed window.\n{_TABLE_B}") is None


# -- 2026-10-05: claim binding (live false positives that FAILED correct answers) --

_NET_PROFIT = [
    -42150.82, -29332.48, -27760.06, -18342.92, -34330.06, -25009.12, -8439.02, 896.89,
    -17026.13, 26819.23, 23524.32, 12000.0, 5000.0, 3000.0, 2000.0, 1000.0, 500.0, 250.0,
    125.0, 100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 5.0,
]


def _net_profit_table() -> str:
    rows = "\n".join(f"| 2026-09-{i + 1:02d} | {v:,.2f} |" for i, v in enumerate(_NET_PROFIT))
    return f"| Date | Net profit (INR) |\n| --- | ---: |\n{rows}\n"


def test_a_period_length_or_a_lowest_value_is_not_read_as_the_total():
    """Live MS3-701d6624a0: "The 30-day total is a net loss of about INR 37.6k
    … (lowest: INR -42.2k …)" was read as asserting -42,200 and FAILED a
    correct answer."""
    total = sum(_NET_PROFIT)
    text = (
        f"Net profit was INR {total:,.2f} (loss).\n\n{_net_profit_table()}\n"
        f"Interpretation: The 30-day total is a net loss of about INR {abs(total) / 1000:.1f}k driven "
        "by large negative days early in the window (lowest: INR -42.2k on 2026-09-01) despite more "
        "positive days overall (17 positive days vs 13 negative days)."
    )
    assert total_mismatch(text) is None


def test_a_wrong_total_after_a_colon_is_still_caught():
    """Splitting on ":" separated "Total spend:" from its figure, so this
    shape was never checked."""
    assert total_mismatch(f"Total spend: INR 9,146,009.\n{_TABLE}") is not None
    assert total_mismatch(f"Total spend: INR 11,943,009.97.\n{_TABLE}") is None


def test_an_overall_rate_inside_the_rows_is_not_a_total():
    """Live MS3-c645523b51: an overall CTR over daily CTR rows was flagged as
    a wrong total and burned three revisions."""
    table = "| Date | CTR |\n| --- | ---: |\n| 2026-09-27 | 0.0201 |\n| 2026-09-28 | 0.0215 |\n| 2026-09-29 | 0.0181 |\n"
    assert total_mismatch(f"Overall CTR for the week was 0.0199.\n\n{table}") is None
    assert total_mismatch(f"Overall, CTR fell by 0.0020 over the week.\n\n{table}") is None


def test_ranks_and_percentages_are_not_totals():
    assert total_mismatch(f"Across the top 5 campaigns the total was 11,943,009.97 (up 12% overall).\n{_TABLE}") is None


def test_per_row_rounding_of_a_long_table_is_not_a_wrong_total():
    """Live MS3-e18a06b408: the exact total the tool returned (32,858.55) vs
    30 rows printed at 2 decimals summing to 32,858.60 — a correct total."""
    rows = [-1095.2851] * 29 + [-1095.2851 - 0.0001]
    table = "| Day | Net profit |\n| --- | ---: |\n" + "".join(f"| d{i} | {v:,.2f} |\n" for i, v in enumerate(rows))
    exact = sum(rows)
    assert abs(exact - sum(round(v, 2) for v in rows)) > 0.1
    assert total_mismatch(f"Net profit was a loss of INR {abs(exact):,.2f} (total).\n\n{table}") is None
    assert total_mismatch(f"Net profit was a loss of INR {abs(exact) + 50:,.2f} (total).\n\n{table}") is not None


@pytest.mark.parametrize(
    ("text", "dangling"),
    [
        ("I don't have a single ", "single"),
        ("Net sales were ₹87,998 for the", "the"),
        ("Hi — how can I help you today?", None),
        ("Net sales were ₹87,998.", None),
        ("| a | b |\n| --- | --- |\n| x | 1 |", None),
        ("Would you like this by channel", None),
        ("Revenue: 1,234 INR", None),
    ],
)
def test_a_cut_off_answer_is_detected(text, dangling):
    from seleric_swarm.agent.validation.answer_audit import cut_off

    assert cut_off(text) == dangling


# -- handing the work back to the reader ----------------------------------------
#
# Live 2026-10-06 (MS3-167d9f4838): the mission fetched one of the two windows the
# question asked about and closed with "Do you want me to (A) fetch today's
# metrics ... or (B) run a diagnose?", shipping as `completed`. INSTRUCTIONS
# forbids this in plain words, so it is checked rather than trusted.


@pytest.mark.parametrize(
    "text",
    [
        # The incident's closing line, after a table.
        "Top 3-day performers (2026-10-03..2026-10-05): PMax.\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "Do you want me to (A) fetch today's metrics, or (B) run a diagnose?",
        # An offer is normally the last sentence but not always the only one.
        "ROAS fell 4%. That is the diagnosis. Would you like me to open a ticket?",
        # Verb-gated leads: about doing the work.
        "Should I fetch today's numbers as well?",
        "Shall I break this down by ad set?",
        "Do you want the same broken down by ad set?",
    ],
)
def test_an_answer_ending_in_an_offer_is_detected(text: str) -> None:
    from seleric_swarm.agent.validation.answer_audit import ends_in_offer

    assert ends_in_offer(text) is not None, text


@pytest.mark.parametrize(
    "text",
    [
        # A genuine blocker: which value to use is only the user's to give, and
        # INSTRUCTIONS explicitly permits asking for it.
        "Which brand should I use — Acme or Globex?",
        "Should I use gross_sales or net_sales for revenue?",
        # Asking which DEFINITION to use is the blocker case, not an offer of
        # work: the user owns that choice, so it must not be flagged.
        "Do you prefer net sales or gross sales here?",
        # A real question with a real answer.
        "Why did ROAS fall? Because spend rose 12% while sales were flat.",
        # The lead-in inside prose is not a hand-back.
        "Totals may shift once refunds post; let me know if the figures disagree.",
        # No question mark: a trailing note, not a hand-back.
        "Let me know if you want the CSV.",
        # A finished answer.
        "ROAS fell from 2.79 to 1.66 today, driven by a 12% spend increase on PMax.",
        "",
    ],
)
def test_a_finished_answer_is_not_flagged_as_an_offer(text: str) -> None:
    from seleric_swarm.agent.validation.answer_audit import ends_in_offer

    assert ends_in_offer(text) is None, text

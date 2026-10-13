"""Offline cases for the deterministic number grounding, each a failure shape seen in a real session.

Ported from /opt/seleric/test_agent/test_grounding.py (sha 006d46a3, synced 2026-10-13).
"""
import datetime
import random

from seleric_swarm.agent.grounding import answer_numbers, grounding, placeholders, problems
from seleric_swarm.agent.mcp_toolset import compact, filter_misuse, flag_composed, flag_partial

CAMP = {"query_id": "q1", "columns": ["ad_spend", "orders"], "rows": [
    {"campaign_name": "TH-012-SNUGBOO-23SEP", "ad_spend": "20193.1", "orders": "15"},
    {"campaign_name": "TH-445-PROSUSPENDERBOOTS-25SEP", "ad_spend": "21743.12", "orders": "11"},
    {"campaign_name": "CAT TOY CS-26SEP", "ad_spend": "28431.31", "orders": "16"}]}


def test_measured_derived_unmatched():
    payloads = [{"provenance": {"query_id": "q0"}, "rows": [{"sales": "8468.48"}], "compare_rows": [{"sales": "34945.2"}], "pct_change": -75.8}]
    g = grounding("Sales fell to ₹8,468 from ₹34,945 (-75.8%), a drop of ₹26,477. Fake: ₹26,077 and 1.2 lakh on 2026-10-12.", payloads)
    assert g["measured"] == 3, g
    assert any("26,477" in d for d in g["derived"]), g
    assert any("26,077" in u for u in g["unmatched"]) and any("1.2 lakh" in u for u in g["unmatched"]), g


def test_answer_numbers_units_and_dates():
    assert [n["value"] for n in answer_numbers("₹1,76,727 and 1.8L and 12 Oct and top 5")] == [176727, 180000]
    assert answer_numbers("2026-10-12T00:00:00 and 14:30 and Oct 12 and 12th Oct and 2026") == []


def test_compact_drops_plumbing_and_caps_lists():
    s = compact({"rows": [{"v": i} for i in range(5000)], "cube_query": "x" * 9999})
    assert "cube_query" not in s and "more items truncated" in s, s[-200:]


def test_misbound_entity_row():
    bad = grounding("**TH-012-SNUGBOO-23SEP**\n- Ad spend: ₹21,743\n- Orders: 15\n", [CAMP])
    assert bad["misbound"] and "21,743" in bad["misbound"][0], bad
    assert "TH-445-PROSUSPENDERBOOTS-25SEP" in bad["misbound"][0], bad
    good = grounding("**TH-012-SNUGBOO-23SEP**\n- Ad spend: ₹20,193\n- Orders: 15\n\n**CAT TOY CS-26SEP**\n- Ad spend: ₹28,431\n", [CAMP])
    assert not good["misbound"] and not good["unmatched"], good
    row = grounding("| TH-445-PROSUSPENDERBOOTS-25SEP | ₹21,743 | 11 |\n| CAT TOY CS-26SEP | ₹28,431 | 16 |", [CAMP])
    assert not row["misbound"], row
    assert grounding("| TH-012-SNUGBOO-23SEP | ₹28,431 | 15 |", [CAMP])["misbound"]
    ratio = grounding("**CAT TOY CS-26SEP**\n- Spend per order: ₹1,777\n", [CAMP])
    assert not ratio["misbound"] and not ratio["unmatched"], ratio


def test_compare_rows_belong_to_the_same_entity():
    cmp_ = {"query_id": "q2", "rows": [{"campaign_name": "A-CAMP", "net_sales": "100.0"}], "compare_rows": [{"campaign_name": "A-CAMP", "net_sales": "70.0"}]}
    assert not grounding("A-CAMP: net sales ₹70 → ₹100", [cmp_])["misbound"]


def test_large_payload_keeps_real_figures():
    many = {"query_id": "q3", "rows": [{"k": f"row{i}", "v": str(1000 + i * 7.31)} for i in range(900)] + [{"k": "tail", "v": "987654.32"}]}
    mid = round(float(many["rows"][273]["v"]))
    assert not grounding(f"Tail row total is ₹9,87,654 and a mid row is ₹{mid:,}.", [many])["unmatched"]


def test_placeholders():
    ph = grounding("Ad spend: ₹16,227.73? → ₹49,163\nSessions: ? → 4,190\nOrders: 7? → 20\nWant the breakdown for 2026-10-11?",
                   [{"rows": [{"a": "16227.73", "b": "49163", "c": "4190", "d": "7", "e": "20"}]}])
    assert ph["placeholders"] == ["₹16,227.73?", "?", "7?"], ph
    assert placeholders("Anything else?") == []


def test_null_cells_survive_compaction():
    assert '"landing_page_views":null' in compact({"rows": [{"campaign_name": "PMax", "landing_page_views": None, "sessions": "3276"}]})


def test_partial_window_warning():
    today = datetime.date(2026, 10, 13)
    p = {"warnings": [], "provenance": {"time_range": {"start": "2026-10-01", "end": "2026-10-13"}}}
    flag_partial(p, today)
    assert any("2026-10-13" in w for w in p["warnings"]), p
    q = {"provenance": {"time_range": {"start": "2026-10-01", "end": "2026-10-12"}}}
    flag_partial(q, today)
    assert not q.get("warnings"), q


def test_pair_scan_is_not_vacuous():
    random.seed(7)
    pool = {"query_id": "q8", "rows": [{"k": f"entity{i}", "v": str(round(random.lognormvariate(9, 1.4), 2))} for i in range(300)]
            + [{"total": "412345.5", "prev": "389000.25"}]}
    fake_hits = sum(not grounding(f"Invented ₹{round(random.uniform(1e3, 5e5)):,}", [pool])["unmatched"] for _ in range(200))
    assert fake_hits <= 40, f"derived check accepts {fake_hits}/200 invented figures"


def test_legitimate_derived_claims_pass():
    q = {"query_id": "q4", "rows": [{"campaign_name": "PMax Seasonal", "net_sales": "107636.0", "ad_spend": "49711.3", "orders": "63", "sessions": "3276"},
                                    {"campaign_name": "Brand Search", "net_sales": "78193.0", "ad_spend": "52972.3", "orders": "46", "sessions": "1786"}],
         "compare_rows": [{"campaign_name": "PMax Seasonal", "net_sales": "40000.0", "ad_spend": "16227.7", "orders": "13", "sessions": "1313"}]}
    tot = {"query_id": "q5", "rows": [{"ad_spend": "703770.0", "net_sales": "1089082.0"}], "compare_rows": [{"ad_spend": "293571.0", "net_sales": "647754.0"}]}
    legit = grounding("**PMax Seasonal**\n- ROAS: 2.2\n- Net sales up ₹67,636 (+169.1%)\n- Share of total spend: 7.1%\n- Sessions to orders: 1.9%\n"
                      "\nTotal net sales grew ₹441,328 (+68.1%).", [q, tot])
    assert not legit["unmatched"] and not legit["misbound"], legit
    assert not grounding("Brand Search spent ₹52,972.", [q])["unmatched"]
    assert grounding("Brand Search spent ₹99,999.", [q])["unmatched"]


def test_set_notset_with_values_is_misuse():
    assert filter_misuse({"filters": [{"dimension": "product_title", "operator": "set", "values": ["A", "B"]}]})
    assert filter_misuse({"additional_filters": [{"dimension": "x", "operator": "notSet", "values": ["A"]}]})
    assert not filter_misuse({"filters": [{"dimension": "x", "operator": "set"}, {"dimension": "y", "operator": "equals", "values": ["A", "B"]}]})


def test_day_series_and_catalogue_payload():
    days = {"query_id": "q9", "rows": [{"report_date": f"2026-10-{d:02d}T00:00:00.000", "net_sales": str(v)} for d, v in
            [(6, 117855.2), (7, 102401.4), (8, 115142.1), (9, 69088.3), (10, 70686.0), (11, 34377.0), (12, 42177.3)]]}
    catalogue = {"matches": [{"view": "commerce", "id": "net_sales", "display_name": "Net sales", "limit": 50}]}
    ans = ("2026-10-11 had the lowest sales — Net sales ₹34,377.\n\nEvidence — Measured (commerce.order_date, q9)\n"
           "- 2026-10-06: ₹117,855 — Measured\n- 2026-10-07: ₹102,401 — Measured\n- 2026-10-11: ₹34,377 — Measured ← lowest\n")
    fp = grounding(ans, [catalogue, days])
    assert not fp["misbound"] and not fp["unmatched"], fp
    wrong = grounding("- 2026-10-11: ₹117,855 — Measured", [catalogue, days])
    assert wrong["misbound"] and "2026-10-06" in wrong["misbound"][0], wrong


def test_metric_name_is_not_an_entity():
    ex = {"provenance": {"query_id": "q6", "metric_ids": ["net_sales", "ad_spend"]},
          "totals": [{"metric_id": "net_sales", "delta": 7800.43, "pct_change": 22.69}, {"metric_id": "ad_spend", "delta": -1419.0, "pct_change": -3.5}]}
    mm = grounding("Ad spend fell 3.5% while net sales rose ₹7,800 (+22.7%).", [ex])
    assert not mm["misbound"] and not mm["unmatched"], mm


def test_composed_parts_with_different_entities_are_flagged():
    comp = {"query_id": "q7", "composition": "multi_view", "parts": [
        {"query_id": "q7a", "rows": [{"campaign_name": "Brand Search", "ad_platform": "google", "order_pnl_net_sales": "78193"}]},
        {"query_id": "q7b", "rows": [{"campaign_name": "Brand Search", "ad_platform": "google", "clicks": "1697"},
                                     {"campaign_name": "Demand Gen", "ad_platform": "google", "clicks": "21805"}]}]}
    flag_composed(comp)
    assert comp["warnings"] and "Demand Gen" in comp["warnings"][0] and "NOT zero" in comp["warnings"][0], comp
    same = {"parts": [{"rows": [{"c": "A", "x": "1"}]}, {"rows": [{"c": "A", "y": "2"}]}]}
    flag_composed(same)
    assert not same.get("warnings"), same


def test_multi_step_arithmetic_is_accepted_invented_step_is_not():
    m = {"query_id": "q10", "rows": [{"net_sales": "600996.0", "net_cogs": "220822.0", "ad_spend": "345581.0"}],
         "compare_rows": [{"net_sales": "551727.0", "net_cogs": "205797.0", "ad_spend": "395584.0"}]}
    chain = grounding("Previous contribution ₹380,174 (600,996 − 220,822); current ₹345,931 (551,727 − 205,797); change −₹34,243. "
                      "Previous contribution ROAS 1.100, current 0.874, a change of −20.5%.", [m])
    assert not chain["unmatched"], chain
    invented = grounding("Previous contribution ₹380,174; current ₹345,931; change −₹99,243.", [m])
    assert any("99,243" in u for u in invented["unmatched"]), invented


def test_problems_text():
    g = grounding("Spend was ₹99,999.", [CAMP])
    assert problems(g) and "99,999" in problems(g)[0]


def test_codes_inside_hyphenated_names_are_not_figures():
    assert answer_numbers("TH-383-SUSPENDER-26SEP-ADV+ and TH-222-WINGBIRD-23SEP, SKU-12") == []
    assert [n["value"] for n in answer_numbers("TH-383-SUSPENDER spent ₹73,137 and net profit was -₹24,562")] == [73137, -24562]
    camp = {"query_id": "q1", "rows": [{"campaign_name": "TH-383-SUSPENDER-26SEP-ADV+", "ad_spend": "73137"}]}
    assert not grounding("TH-383-SUSPENDER-26SEP-ADV+: ad spend ₹73,137", [camp])["unmatched"]


def test_non_breaking_hyphen_in_a_name_is_still_a_code():
    assert answer_numbers("TH\u2011149\u2011SCRATCHLOUNGE and TH\u2011383\u2011SUSPENDER") == []


def test_digits_glued_to_a_name_suffix_are_not_figures():
    # live 2026-10-13: "PawTech Cleaner with Paw Cleanser 500ml" was flagged as an ungrounded 500
    assert answer_numbers("PawTech Cleaner with Paw Cleanser 500ml, 4XL boots, 26SEP campaign") == []
    assert [n["value"] for n in answer_numbers("₹1.2L, 5k orders and 3x ROAS")] == [120000, 5000, 3]
    product = {"query_id": "q1", "rows": [{"product_title": "PawTech Cleaner with Paw Cleanser 500ml", "gross": "80641.2"}]}
    assert not grounding("| PawTech Cleaner with Paw Cleanser 500ml | 80,641 |", [product])["unmatched"]


def test_correction_text_keeps_what_the_user_asked_for():
    from seleric_swarm.agent.grounding import correction_message

    text = correction_message(["figures in no tool result: 99,999"])
    assert "99,999" in text and "every table, row, breakdown" in text
    assert "query the period total instead" not in text  # the old wording sent the model to grand totals


def test_a_correction_that_loses_the_verified_content_is_detected():
    from seleric_swarm.agent.grounding import lost_substance

    data = {"query_id": "q", "rows": [{"p": f"Product {c}", "v": str(1000 * (i + 1) + 0.4)} for i, c in enumerate("ABCDEFGH")]}
    table = "\n".join(f"| Product {c} | {1000 * (i + 1):,} |" for i, c in enumerate("ABCDEFGH")) + "\n| Product Z | 99,999 |"
    draft = grounding(table, [data])
    assert draft["unmatched"] and draft["measured"] == 8
    assert lost_substance(draft, grounding("Total was ₹36,000.", [data]))  # table traded for one figure
    assert not lost_substance(draft, grounding(table.replace("99,999", "9,000"), [data]))  # a real fix


def test_a_percentage_must_follow_from_the_figures_beside_it():
    # live 2026-10-13 Q1: "34,377 -> 42,177 (+23.0%)" passed (true change +22.7%) through a coincidental pair in the data
    data = {"query_id": "q", "rows": [{"net_sales": "42177.33", "gross": "46782", "orders": "26"}],
            "compare_rows": [{"net_sales": "34376.9", "gross": "36166", "orders": "19"}]}
    wrong = grounding("Net sales 2026-10-11: ₹34,377 → 2026-10-12: ₹42,177 (+23.0%).", [data])
    assert "23.0%" in wrong["unmatched"], wrong
    right = grounding("Net sales 2026-10-11: ₹34,377 → 2026-10-12: ₹42,177 (+22.7%).", [data])
    assert not right["unmatched"], right
    # one operand on the line is not enough to bind it: the other may be stated elsewhere
    assert not grounding("Orders rose 36.8% (to 26).", [data])["unmatched"]

"""Session email drafts: the MoM template (sections, subject, escaping) and the free-form ad hoc email."""

from __future__ import annotations

from seleric_swarm.mail.draft import ActionItem, AdHoc, EmailTable, MoM, render_ad_hoc, render_mom


def _mom(**kw) -> MoM:
    return MoM(topic="Checkout drop-off", date="2026-10-28", **kw)


def test_mom_subject_uses_middle_dots() -> None:
    assert render_mom(_mom(summary="s")).subject == "MoM · Checkout drop-off · 2026-10-28"


def test_mom_omits_empty_sections() -> None:
    out = render_mom(_mom(summary="We looked at checkout.", discussion_points=["Completion fell 12%"]))
    assert "Summary" in out.text and "Key discussion points" in out.text
    for absent in ("Decisions made", "Action items", "Open items", "Attachments"):
        assert absent not in out.text and absent not in out.html


def test_mom_action_item_shows_only_given_owner_and_deadline() -> None:
    out = render_mom(_mom(action_items=[ActionItem("Audit the payment step"),
                                        ActionItem("Fix the coupon bug", owner="Ajay", deadline="2026-11-02")]))
    assert "- Audit the payment step\n" in out.text  # no owner / deadline invented
    assert "- Fix the coupon bug (owner: Ajay, due: 2026-11-02)" in out.text
    assert "owner: None" not in out.text and "due: None" not in out.text


def test_mom_has_html_and_text_and_escapes_content() -> None:
    out = render_mom(_mom(summary="<script>alert(1)</script> & more"))
    assert "<script>" not in out.html and "&lt;script&gt;" in out.html
    assert "<script>" in out.text  # plain text is not HTML: nothing to escape
    assert ">Summary</h2>" in out.html  # styled inline, so no bare <h2>


def test_ad_hoc_uses_the_subject_and_body_it_was_given() -> None:
    out = render_ad_hoc(AdHoc(subject="Please investigate the checkout drop-off",
                              body="Hi Ajay,\n\nCompletion fell 12%.\n\n- step 1\n- step 2\n\nThanks,\nGaurav"))
    assert out.subject == "Please investigate the checkout drop-off"
    assert out.text.startswith("Hi Ajay,")
    assert "<p>Hi Ajay,</p>" in out.html and "<li>step 1</li>" in out.html
    assert "MoM" not in out.subject and "Summary" not in out.html  # not a template


CHANNELS = EmailTable(
    title="Net sales by channel", subtitle="2026-10-21 to 2026-10-27", headers=["Channel", "Net sales (INR)", "Share"],
    rows=[["ig_feed", "₹1,31,269", "38.3%"], ["google_pmax", "₹1,03,521", "30.2%"]], align=["l", "r", "r"],
    note="Showing 2 of 12 rows. Full data in the attachment.")


def test_a_table_goes_where_the_placeholder_is_and_is_a_real_html_table() -> None:
    body = "Hi Gaurav,\n\n## What happened\n\nSales fell **10.8%**.\n\n[[table:q_ch]]\n\nThanks,\nSeleric Reporting Assistant"
    out = render_ad_hoc(AdHoc("Net sales fell 10.8%", body, {"q_ch": [CHANNELS]}))
    assert out.html.index("What happened") < out.html.index("<table") < out.html.index("Thanks,")  # not appended after the sign-off
    assert "<strong>10.8%</strong>" in out.html and ">Net sales by channel<" in out.html
    assert out.html.count("<th") == 3 and "₹1,31,269" in out.html and "Showing 2 of 12 rows" in out.html
    assert "text-align:right" in out.html  # numbers line up on the right
    assert "| Channel | Net sales (INR) | Share |" in out.markdown and "---:" in out.markdown


def test_the_plain_text_fallback_has_an_aligned_table_and_no_markup() -> None:
    out = render_ad_hoc(AdHoc("s", "## Data\n\n[[table:q_ch]]\n\nThanks", {"q_ch": [CHANNELS]}))
    assert "##" not in out.text and "**" not in out.text and "|" not in out.text
    lines = [ln for ln in out.text.splitlines() if "ig_feed" in ln or "google_pmax" in ln]
    assert len(lines) == 2 and lines[0].index("₹1,31,269") + len("₹1,31,269") == lines[1].index("₹1,03,521") + len("₹1,03,521")


def test_a_requested_table_without_a_placeholder_is_still_in_the_email() -> None:
    out = render_ad_hoc(AdHoc("s", "Hi,\n\nSee below.", {"q_ch": [CHANNELS]}))
    assert "<table" in out.html and "ig_feed" in out.text


def test_inline_table_placeholders_are_stripped_from_prose() -> None:
    body = ("Hi Gaurav,\n\n## What happened\n\n"
            "- Net sales were ₹56,146 ([[table:q_ch]]).\n"
            "- Orders were 27.\n\nThanks")
    out = render_ad_hoc(AdHoc("s", body, {"q_ch": [CHANNELS]}))
    assert "[[table:" not in out.markdown and "[[table:" not in out.html and "[[table:" not in out.text
    assert "Net sales were ₹56,146." in out.text
    assert "<table" in out.html and "ig_feed" in out.text


def test_email_html_tables_hug_content_not_full_width() -> None:
    out = render_ad_hoc(AdHoc("s", "[[table:q_ch]]", {"q_ch": [CHANNELS]}))
    table_tag = out.html.split("<table", 1)[1].split(">", 1)[0]
    assert "width:auto" in table_tag and "max-width:100%" in table_tag
    assert "width:100%" not in table_tag.replace("max-width:100%", "")


def test_table_cells_and_titles_are_escaped() -> None:
    evil = EmailTable("<b>t</b>", "", ["Campaign"], [["<script>x</script> | pipe"]], ["l"])
    out = render_ad_hoc(AdHoc("s", "[[table:q]]", {"q": [evil]}))
    assert "<script>" not in out.html and "&lt;script&gt;" in out.html and "<b>t</b>" not in out.html


def test_a_mom_shows_its_data_tables_under_a_data_section() -> None:
    out = render_mom(_mom(summary="s", tables=[CHANNELS]))
    assert ">Data</h2>" in out.html and "<table" in out.html and "ig_feed" in out.text
    assert "Data" not in render_mom(_mom(summary="s")).text  # no tables, no section


def test_the_email_is_a_complete_html_document() -> None:
    html = render_ad_hoc(AdHoc("s", "Hi")).html
    assert html.startswith("<!doctype html>") and '<meta charset="utf-8">' in html

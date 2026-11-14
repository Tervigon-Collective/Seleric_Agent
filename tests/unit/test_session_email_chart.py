"""Charts in a session email: drawn from stored rows, placed by [[chart:ID]], embedded inline (cid) in the MIME body."""

from __future__ import annotations

from email import message_from_bytes, policy

from seleric_swarm.mail.chart import chart_png
from seleric_swarm.mail.draft import AdHoc, EmailChart, render_ad_hoc
from seleric_swarm.mail.excel import Column, Dataset
from seleric_swarm.mail.graph import to_mime
from seleric_swarm.mail.send import Attachment, Draft

_ROWS = [{"period_start": f"2026-10-2{d}", "gross_sales": 1000.0 * d} for d in range(1, 8)]
_DS = Dataset("Gross sales", [Column("period_start", "Date", "date"), Column("gross_sales", "Gross sales", "currency")], _ROWS)


def test_trend_dataset_becomes_a_png() -> None:
    assert (chart_png(_DS) or b"").startswith(b"\x89PNG")


def test_window_total_and_single_point_have_no_chart() -> None:
    total = Dataset("Total", [Column("period_start", "Start", "date"), Column("period_end", "End", "date"),
                              Column("gross_sales", "Gross sales", "currency")], _ROWS)
    assert chart_png(total) is None and chart_png(Dataset("One", _DS.columns, _ROWS[:1])) is None


def test_chart_placeholder_renders_an_inline_image_where_placed() -> None:
    out = render_ad_hoc(AdHoc("S", "Intro\n\n[[chart:q1]]\n\nBye", charts={"q1": [EmailChart("Gross sales", "c1@seleric")]}))
    assert 'src="cid:c1@seleric"' in out.html and out.html.index("Intro") < out.html.index("cid:") < out.html.index("Bye")
    assert "[[chart" not in out.html + out.text + out.markdown


def test_mime_embeds_the_image_inline_next_to_the_file_attachment() -> None:
    png = chart_png(_DS) or b""
    d = Draft("a@seleric.ai", ("b@seleric.ai",), "S", '<p><img src="cid:c1@seleric"></p>', "t",
              attachments=(Attachment("data.xlsx", b"PK"), Attachment("gross.png", png, "image/png", "c1@seleric")))
    msg = message_from_bytes(to_mime(d), policy=policy.default)
    related = [p for p in msg.walk() if p.get_content_type() == "multipart/related"]
    assert related and related[0].get_payload()[-1]["Content-ID"] == "<c1@seleric>"
    assert [a.get_filename() for a in msg.iter_attachments() if a.get_content_type() != "multipart/alternative"
            and a.get_filename()] == ["data.xlsx"]

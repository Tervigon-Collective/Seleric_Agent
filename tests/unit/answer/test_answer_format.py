"""Answer text reads like prose: figures are named, rows are named, nothing prints twice."""

from __future__ import annotations

from datetime import date

from seleric_swarm.agent.validation.answer_audit import strip_markdown_tables
from seleric_swarm.answer import Evidence, render
from seleric_swarm.answer._render import plain_words
from seleric_swarm.answer._text import bare_figures
from seleric_swarm.contracts.pipeline import (
    AnswerDraft,
    Claim,
    Paragraph,
    RankingChartBlock,
    RankingTableBlock,
    Ref,
    ResultRow,
    ResultSet,
    WindowSpec,
)
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [{"id": "zz_take", "label": "Take", "unit": "INR", "aggregation": "additive",
                 "supported_dimensions": ["zz_zone_name"]}],
    "dimensions": [{"id": "zz_zone_name"}],
})
WIN = WindowSpec(role="event", start=date(2026, 10, 9), end=date(2026, 10, 9))
RS = ResultSet(
    ref=Ref(kind="result_set", id="q1"), step_id="q1", metric_id="zz_take", unit="INR", window=WIN,
    breakdowns=("zz_zone_name",), filters=(),
    rows=(
        ResultRow(evidence_id="q1-0", dimensions={"zz_zone_name": "north"}, value=900.0),
        ResultRow(evidence_id="q1-1", dimensions={"zz_zone_name": "south"}, value=600.0),
    ),
)
EVIDENCE = Evidence([RS])
TABLE = RankingTableBlock(kind="ranking_table", result_set=RS.ref, columns=("zz_zone_name", "value"),
                          caption="Ranked by Take", criterion_label="Take")
CHART = RankingChartBlock(kind="ranking_chart", ref=RS.ref, title="Top by Take", criterion_label="Take",
                          name_key="zz_zone_name", id_key="zz_zone_name")


def _draft(*blocks, claims=()) -> AnswerDraft:
    return AnswerDraft(status="completed", claims=claims, blocks=blocks)


def _claim(cid: str, zone: str) -> Claim:
    return Claim(claim_id=cid, ref=RS.ref, row_key={"zz_zone_name": zone}, label="x", role="support")


def test_bare_figure_list_is_named_but_an_introduced_figure_is_not() -> None:
    assert bare_figures("Leaders: {a}, {b}, and {c}.") == {"a", "b", "c"}
    assert bare_figures("Take rose to {a}, up {b} on the week.") == {"b"}
    assert bare_figures("It ran from {a} to {b}.") == set()


def test_bare_figures_say_what_they_are() -> None:
    draft = _draft(Paragraph(kind="paragraph", text="Leaders: {a}, {b}."),
                   claims=(_claim("a", "north"), _claim("b", "south")))
    text = render(draft, EVIDENCE, MODEL).rendered_markdown
    assert "INR 900 (north, Take), INR 600 (south, Take)." in text


def test_ranking_rows_are_named_never_a_dict_and_not_listed_beside_their_table() -> None:
    both = render(_draft(TABLE, CHART), EVIDENCE, MODEL)
    assert "| Zz zone name | Take |" in both.rendered_markdown
    assert "Top by Take" not in both.rendered_markdown          # table already printed it
    assert [p["type"] for p in both.parts] == ["TABLE", "CHART"]  # the widgets still ship

    alone = render(_draft(CHART), EVIDENCE, MODEL).rendered_markdown
    assert "- north: INR 900" in alone and "{" not in alone


def test_stripping_a_table_takes_its_caption() -> None:
    text = "Intro.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n_Ranked by Take_\n\nOutro."
    assert strip_markdown_tables(text) == "Intro.\n\nOutro."


def test_identifier_tokens_in_limitations_read_as_words() -> None:
    assert plain_words("Many rows_not_shown; see the Take total.") == "Many rows not shown; see the Take total."
    assert plain_words("Keep snake_Case and 2026-10-09 intact.") == "Keep snake_Case and 2026-10-09 intact."


def test_a_rows_id_dimension_is_not_shown_beside_its_label() -> None:
    model = SemanticReadModel.from_payload({
        "catalogue_version": "zz-v1",
        "metrics": [{"id": "zz_take", "label": "Take", "unit": "INR", "aggregation": "additive",
                     "supported_dimensions": ["zz_zone_name", "zz_zone_id"]}],
        "dimensions": [{"id": "zz_zone_id"}, {"id": "zz_zone_name", "stable_key": "zz_zone_id"}],
    })
    rows = ResultSet(
        ref=Ref(kind="result_set", id="q1"), step_id="q1", metric_id="zz_take", unit="INR", window=WIN,
        breakdowns=("zz_zone_id", "zz_zone_name"), filters=(),
        rows=(ResultRow(evidence_id="q1-0", dimensions={"zz_zone_id": "9001", "zz_zone_name": "north"}, value=900.0),),
    )
    claim = Claim(claim_id="a", ref=rows.ref, row_key={"zz_zone_id": "9001", "zz_zone_name": "north"},
                  label="x", role="support")
    doc = render(_draft(Paragraph(kind="paragraph", text="Leaders: {a}."), claims=(claim,)), Evidence([rows]), model)
    assert "INR 900 (north, Take)" in doc.rendered_markdown and "9001 (" not in doc.rendered_markdown


def test_a_figure_whose_row_the_paragraph_never_names_gets_it_attached() -> None:
    draft = _draft(Paragraph(kind="paragraph", text="The top row reached {a}; the other reached {b}."),
                   claims=(_claim("a", "north"), _claim("b", "south")))
    text = render(draft, EVIDENCE, MODEL).rendered_markdown
    assert "reached INR 900 (north); the other reached INR 600 (south)." in text

    named = _draft(Paragraph(kind="paragraph", text="north reached {a}."), claims=(_claim("a", "north"),))
    assert render(named, EVIDENCE, MODEL).rendered_markdown.startswith("north reached INR 900.")


def test_a_sentence_citing_an_unresolvable_figure_is_left_out() -> None:
    draft = _draft(Paragraph(kind="paragraph", text="north reached {a}. The insight says {ghost}. Done."),
                   claims=(_claim("a", "north"),))
    text = render(draft, EVIDENCE, MODEL).rendered_markdown
    assert text.startswith("north reached INR 900. Done.") and "not available" not in text


def test_figures_written_back_to_back_do_not_run_together() -> None:
    draft = _draft(Paragraph(kind="paragraph", text="north and south: {a}{b}"),
                   claims=(_claim("a", "north"), _claim("b", "south")))
    text = render(draft, EVIDENCE, MODEL).rendered_markdown
    assert "INR 900" in text and "INR 900INR" not in text and ", INR 600" in text

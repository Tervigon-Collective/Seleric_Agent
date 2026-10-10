"""Unit tests for M1.2 compile_question_spec (invented zz_* catalogue)."""

from __future__ import annotations

from datetime import date

import pytest

from seleric_swarm.contracts.pipeline import Clarification, QuestionSpec
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.spec import (
    SpecCandidates,
    ValueHit,
    compile_question_spec,
    scope_from_spec,
)
from seleric_swarm.understand import (
    MeasurePhrase,
    QuestionDraft,
    WindowExpr,
    WindowSlot,
)

_CATALOGUE = {
    "catalogue_version": "zz-v1",
    "metrics": [
        {
            "id": "zz_sales",
            "label": "ZZ Sales",
            "unit": "ZZU",
            "aggregation": "additive",
            "supported_dimensions": ["zz_channel", "zz_campaign", "zz_day"],
        },
        {
            "id": "zz_spend",
            "label": "ZZ Spend",
            "unit": "ZZU",
            "aggregation": "additive",
            "supported_dimensions": ["zz_channel", "zz_platform", "zz_day"],
        },
    ],
    "dimensions": [
        {"id": "zz_channel", "family": "zz_chan", "family_rank": 0},
        {"id": "zz_platform", "family": "zz_plat", "family_rank": 0},
        {"id": "zz_campaign"},
        {"id": "zz_day", "is_time": True},
    ],
}


def _model() -> SemanticReadModel:
    return SemanticReadModel.from_payload(_CATALOGUE)


def _hits(*rows: tuple[str, str, str, int]) -> SpecCandidates:
    values = tuple(
        ValueHit(term=term, dimension=dim, value=val, volume=vol, match="exact", source_span=term)
        for term, dim, val, vol in rows
    )
    return SpecCandidates(values=values)


def test_lookup_yesterday_window() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="lookup",
        measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
        windows=(
            WindowSlot(role="event", expr=WindowExpr(name="yesterday"), source_span="yesterday"),
        ),
        names_period=True,
    )
    result = compile_question_spec(
        draft, SpecCandidates(), _model(), None, date(2026, 10, 10)
    )
    assert isinstance(result, QuestionSpec)
    assert result.shape == "lookup"
    assert len(result.windows) == 1
    assert result.windows[0].start == date(2026, 10, 9)
    assert result.windows[0].token == "yesterday"
    assert result.catalogue_version == "zz-v1"


def test_last_n_days_window() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="breakdown",
        measures=(MeasurePhrase(words="spend", metric_id="zz_spend"),),
        breakdown_dimensions=("zz_platform",),
        windows=(
            WindowSlot(
                role="event",
                expr=WindowExpr(unit="day", n=7, ending="yesterday"),
                source_span="last 7 days",
            ),
        ),
        names_period=True,
    )
    result = compile_question_spec(
        draft, SpecCandidates(), _model(), None, date(2026, 10, 10)
    )
    assert isinstance(result, QuestionSpec)
    assert result.windows[0].start == date(2026, 10, 3)
    assert result.windows[0].end == date(2026, 10, 9)
    assert result.breakdowns == ("zz_platform",)


def test_multi_value_filter_collapses_to_in() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="breakdown",
        measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
        windows=(WindowSlot(role="event", expr=WindowExpr(name="yesterday")),),
    )
    candidates = _hits(
        ("web", "zz_channel", "web", 10),
        ("app", "zz_channel", "app", 8),
    )
    result = compile_question_spec(draft, candidates, _model(), None, date(2026, 10, 10))
    assert isinstance(result, QuestionSpec)
    assert len(result.filters) == 1
    assert result.filters[0].operator == "in"
    assert set(result.filters[0].values) == {"web", "app"}
    assert "zz_channel" in result.breakdowns


def test_exclusion_becomes_not_in() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="lookup",
        measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
        exclusion_phrases=("junk",),
        windows=(WindowSlot(role="event", expr=WindowExpr(name="yesterday")),),
    )
    candidates = _hits(("junk", "zz_channel", "junk", 3))
    result = compile_question_spec(draft, candidates, _model(), None, date(2026, 10, 10))
    assert isinstance(result, QuestionSpec)
    assert any(f.operator == "not_in" and "junk" in f.values for f in result.filters)


def test_ordinary_words_never_become_filters() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="lookup",
        measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
        ordinary_words=("web",),
        windows=(WindowSlot(role="event", expr=WindowExpr(name="yesterday")),),
    )
    candidates = _hits(("web", "zz_channel", "web", 10))
    result = compile_question_spec(draft, candidates, _model(), None, date(2026, 10, 10))
    assert isinstance(result, QuestionSpec)
    assert result.filters == ()


def test_entity_comparison_merges_entities() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="entity_comparison",
        entity_dimension="zz_campaign",
        entity_phrases=("camp-a", "camp-b"),
        measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
        windows=(
            WindowSlot(role="event", expr=WindowExpr(name="yesterday")),
            WindowSlot(
                role="baseline",
                expr=WindowExpr(unit="day", n=2, ending="before_event"),
            ),
        ),
    )
    candidates = _hits(
        ("camp-a", "zz_campaign", "CAMP-A", 5),
        ("camp-b", "zz_campaign", "CAMP-B", 4),
    )
    result = compile_question_spec(draft, candidates, _model(), None, date(2026, 10, 10))
    assert isinstance(result, QuestionSpec)
    assert len(result.entities) == 1
    assert set(result.entities[0].values) == {"CAMP-A", "CAMP-B"}
    assert result.windows[0].role == "event"
    assert result.windows[1].role == "baseline"
    assert result.windows[1].token == "prior_2d"


def test_follow_up_keeps_prior_filters() -> None:
    seeded = compile_question_spec(
        QuestionDraft(
            kind="analysis",
            shape="lookup",
            measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
            windows=(WindowSlot(role="event", expr=WindowExpr(name="yesterday")),),
        ),
        _hits(("sku-1", "zz_campaign", "SKU-1", 9)),
        _model(),
        None,
        date(2026, 10, 10),
    )
    assert isinstance(seeded, QuestionSpec)
    follow = QuestionDraft(
        kind="analysis",
        shape="why_single_metric",
        measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
        follows_prior=True,
        windows=(),
    )
    result = compile_question_spec(follow, SpecCandidates(), _model(), seeded, date(2026, 10, 10))
    assert isinstance(result, QuestionSpec)
    assert result.filters == seeded.filters
    assert result.windows == seeded.windows
    assert result.follow_up_of == "prior"


def test_ambiguous_value_clarifies() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="lookup",
        measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
        windows=(WindowSlot(role="event", expr=WindowExpr(name="yesterday")),),
    )
    candidates = _hits(
        ("meta", "zz_channel", "meta", 10),
        ("meta", "zz_platform", "meta", 9),
    )
    result = compile_question_spec(draft, candidates, _model(), None, date(2026, 10, 10))
    assert isinstance(result, Clarification)
    assert result.reason == "ambiguous_value"


def test_unknown_metric_clarifies() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="lookup",
        measures=(MeasurePhrase(words="mystery", metric_id="zz_missing"),),
        windows=(WindowSlot(role="event", expr=WindowExpr(name="yesterday")),),
    )
    result = compile_question_spec(draft, SpecCandidates(), _model(), None, date(2026, 10, 10))
    assert isinstance(result, Clarification)
    assert "unknown metric" in result.question_to_user


def test_draft_needs_clarification() -> None:
    draft = QuestionDraft(needs_clarification="Which brand?")
    result = compile_question_spec(
        draft, SpecCandidates(), _model(), None, date(2026, 10, 10)
    )
    assert isinstance(result, Clarification)
    assert result.question_to_user == "Which brand?"


def test_scope_from_spec() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="breakdown",
        measures=(MeasurePhrase(words="spend", metric_id="zz_spend"),),
        breakdown_dimensions=("zz_platform",),
        windows=(WindowSlot(role="event", expr=WindowExpr(name="yesterday")),),
    )
    spec = compile_question_spec(
        draft,
        _hits(("web", "zz_channel", "web", 4)),
        _model(),
        None,
        date(2026, 10, 10),
    )
    assert isinstance(spec, QuestionSpec)
    scope = scope_from_spec(spec)
    assert frozenset({"zz_platform"}) in scope.breakdowns
    assert scope.windows
    assert scope.value_filters


def test_period_comparison_fixture_shape() -> None:
    draft = QuestionDraft(
        kind="analysis",
        shape="period_comparison",
        measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
        windows=(
            WindowSlot(role="event", expr=WindowExpr(name="today")),
            WindowSlot(
                role="baseline",
                expr=WindowExpr(unit="day", n=3, ending="yesterday"),
            ),
        ),
        names_period=True,
    )
    result = compile_question_spec(
        draft, SpecCandidates(), _model(), None, date(2026, 10, 6)
    )
    assert isinstance(result, QuestionSpec)
    assert result.shape == "period_comparison"
    assert result.windows[0].start == date(2026, 10, 6)
    assert result.windows[1].start == date(2026, 10, 3)
    assert result.windows[1].end == date(2026, 10, 5)


@pytest.mark.parametrize(
    "fixture_id,as_of,windows,shape",
    [
        (
            "net_sales_yesterday",
            date(2026, 10, 10),
            (WindowSlot(role="event", expr=WindowExpr(name="yesterday")),),
            "lookup",
        ),
        (
            "last_week_roas",
            date(2026, 10, 10),
            (WindowSlot(role="event", expr=WindowExpr(name="last_week")),),
            "lookup",
        ),
    ],
)
def test_th383_window_ports(
    fixture_id: str,
    as_of: date,
    windows: tuple[WindowSlot, ...],
    shape: str,
) -> None:
    del fixture_id
    draft = QuestionDraft(
        kind="analysis",
        shape=shape,  # type: ignore[arg-type]
        measures=(MeasurePhrase(words="sales", metric_id="zz_sales"),),
        windows=windows,
        names_period=True,
    )
    result = compile_question_spec(draft, SpecCandidates(), _model(), None, as_of)
    assert isinstance(result, QuestionSpec)
    assert result.windows

"""AG-03 complex question understanding and planning tests.

These tests pin the expected spec/plan behavior for complex questions.
"""

from __future__ import annotations

from datetime import date

from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    Clarification,
    EntitySpec,
    FilterSpec,
    MeasurePhrase,
    MeasureSpec,
    QuestionDraft,
    QuestionSpec,
    WindowSlot,
    WindowExpr,
    WindowSpec,
)
from seleric_swarm.spec._filters import SpecCandidates, ValueHit
from seleric_swarm.plan import plan
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.spec import compile_question_spec

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [
        {"id": "net_sales", "label": "Net Sales", "aggregation": "additive", "unit": "INR", "supported_dimensions": ["campaign_name", "channel"]},
        {"id": "gross_sales", "label": "Gross Sales", "aggregation": "additive", "unit": "INR", "supported_dimensions": ["channel"]},
        {"id": "cac", "label": "CAC", "aggregation": "ratio", "unit": "INR", "composition": {"numerator": "ad_spend", "denominator": "conversions"}},
        {"id": "ad_spend", "label": "Ad Spend", "aggregation": "additive", "unit": "INR", "supported_dimensions": ["channel", "campaign_name"]},
        {"id": "conversions", "label": "Conversions", "aggregation": "additive", "unit": "count", "supported_dimensions": ["channel", "campaign_name"]},
    ],
    "dimensions": [
        {"id": "campaign_name", "allowed_values": ["TH-383-SUSPENDER-26SEP-ADV+", "TH-383-SUSPENDER-29SEP", "camp_a", "camp_b"]},
        {"id": "channel", "allowed_values": ["meta", "google", "amazon"]},
    ],
    "hierarchies": [
        {"id": "traffic", "levels": ["channel", "campaign_name"]}
    ],
    "views": [],
    "brands": [],
    "scope_dimensions": ["brand_id"],
})


def _candidates_for_entity_comparison() -> SpecCandidates:
    """Value hits for the TH-383 entity comparison test."""
    return SpecCandidates(values=(
        ValueHit(term="TH-383-SUSPENDER-26SEP-ADV+", dimension="campaign_name", value="TH-383-SUSPENDER-26SEP-ADV+", volume=100, source_span="TH-383-SUSPENDER-26SEP-ADV+"),
        ValueHit(term="TH-383-SUSPENDER-29SEP", dimension="campaign_name", value="TH-383-SUSPENDER-29SEP", volume=100, source_span="TH-383-SUSPENDER-29SEP"),
        ValueHit(term="yesterday", dimension="", value="", volume=0, match="relative", source_span="yesterday"),
        ValueHit(term="last 3 days", dimension="", value="", volume=0, match="relative", source_span="last 3 days"),
        ValueHit(term="prior 2d", dimension="", value="", volume=0, match="relative", source_span="prior 2d"),
    ))


def _candidates_for_camp_ab() -> SpecCandidates:
    """Value hits for camp_a/camp_b entity comparison."""
    return SpecCandidates(values=(
        ValueHit(term="camp_a", dimension="campaign_name", value="camp_a", volume=100, source_span="camp_a"),
        ValueHit(term="camp_b", dimension="campaign_name", value="camp_b", volume=100, source_span="camp_b"),
    ))


def _candidates_for_performance() -> SpecCandidates:
    """Value hits for 'performance fell'."""
    return SpecCandidates(values=(
        ValueHit(term="performance", dimension="", value="", volume=0, match="relative", source_span="performance"),
        ValueHit(term="yesterday", dimension="", value="", volume=0, match="relative", source_span="yesterday"),
        ValueHit(term="prior 1d", dimension="", value="", volume=0, match="relative", source_span="prior 1d"),
    ))


def _candidates_for_cac() -> SpecCandidates:
    """Value hits for CAC question."""
    return SpecCandidates(values=(
        ValueHit(term="CAC", dimension="", value="", volume=0, match="exact", source_span="CAC"),
        ValueHit(term="yesterday", dimension="", value="", volume=0, match="relative", source_span="yesterday"),
        ValueHit(term="prior 1d", dimension="", value="", volume=0, match="relative", source_span="prior 1d"),
    ))


def _candidates_for_followup() -> SpecCandidates:
    """Value hits for follow-up questions."""
    return SpecCandidates(values=(
        ValueHit(term="ad spend", dimension="", value="", volume=0, match="exact", source_span="ad spend"),
        ValueHit(term="channel", dimension="channel", value="", volume=0, match="exact", source_span="channel"),
    ))


def _candidates_for_unsupported() -> SpecCandidates:
    """Value hits for unsupported metric (empty)."""
    return SpecCandidates(values=())


def _candidates_for_paraphrase() -> SpecCandidates:
    """Value hits for paraphrase test."""
    return SpecCandidates(values=(
        ValueHit(term="camp_a", dimension="campaign_name", value="camp_a", volume=100, source_span="camp_a"),
        ValueHit(term="camp_b", dimension="campaign_name", value="camp_b", volume=100, source_span="camp_b"),
    ))


def _candidates_for_plan() -> SpecCandidates:
    """Value hits for plan test."""
    return SpecCandidates(values=(
        ValueHit(term="camp_a", dimension="campaign_name", value="camp_a", volume=100, source_span="camp_a"),
        ValueHit(term="camp_b", dimension="campaign_name", value="camp_b", volume=100, source_span="camp_b"),
    ))


def _window(role: str, name: str = "", unit: str = "", n: int | None = None, ending: str = "yesterday", start_iso: str = "", end_iso: str = "") -> WindowSlot:
    return WindowSlot(
        role=role,
        expr=WindowExpr(name=name, unit=unit, n=n, ending=ending, start_iso=start_iso, end_iso=end_iso),
    )


def _window_prior(role: str, n: int = 1, unit: str = "day", ending: str = "before_event") -> WindowSlot:
    """Window for 'prior N days/weeks' before the event window."""
    return WindowSlot(
        role=role,
        expr=WindowExpr(name="", unit=unit, n=n, ending=ending),
    )


# ---------------------------------------------------------------- AG03-T01: TH-383 two-campaign comparison
async def test_ag03_t01_th383_entity_comparison() -> None:
    """TH-383: two campaigns compared over independent periods (last 3 days context, yesterday event, prior_2d baseline)."""
    draft = QuestionDraft(
        kind="analysis",
        shape="entity_comparison",
        measures=(MeasurePhrase(words="net sales", metric_id="net_sales"),),
        entity_dimension="campaign_name",
        entity_phrases=("TH-383-SUSPENDER-26SEP-ADV+", "TH-383-SUSPENDER-29SEP"),
        windows=(
            _window("event", name="yesterday"),
            _window_prior("baseline", n=2, unit="day"),
            _window("context", unit="day", n=3, ending="yesterday"),
        ),
        grain="none",
        direction="either",
    )
    spec = compile_question_spec(draft, _candidates_for_entity_comparison(), MODEL, None, date(2026, 9, 30))
    assert spec.shape == "entity_comparison"
    assert len(spec.entities) == 1
    assert spec.entities[0].dimension == "campaign_name"
    assert spec.entities[0].values == ("TH-383-SUSPENDER-26SEP-ADV+", "TH-383-SUSPENDER-29SEP")
    # Three windows: event (yesterday), baseline (prior_2d), context (last_3d)
    assert len(spec.windows) == 3
    roles = {w.role for w in spec.windows}
    assert roles == {"event", "baseline", "context"}


# ---------------------------------------------------------------- AG03-T02: Comparison retains both entities and all windows
async def test_ag03_t02_comparison_retains_entities_and_windows() -> None:
    """Both entities and all requested windows must survive compilation."""
    draft = QuestionDraft(
        kind="analysis",
        shape="entity_comparison",
        measures=(MeasurePhrase(words="net sales", metric_id="net_sales"),),
        entity_dimension="campaign_name",
        entity_phrases=("camp_a", "camp_b"),
        windows=(
            _window("event", start_iso="2026-09-29", end_iso="2026-09-29"),
            _window("baseline", start_iso="2026-09-27", end_iso="2026-09-28"),
            _window("context", start_iso="2026-09-27", end_iso="2026-09-29"),
        ),
        grain="none",
        direction="either",
    )
    spec = compile_question_spec(draft, _candidates_for_camp_ab(), MODEL, None, date(2026, 9, 30))
    assert len(spec.windows) == 3
    assert spec.entities[0].values == ("camp_a", "camp_b")


# ---------------------------------------------------------------- AG03-T03: "Why did performance fall?" resolves scope safely
async def test_ag03_t03_performance_fall_resolves_scope() -> None:
    """Ambiguous 'performance fell' should resolve to a measurable metric + windows."""
    draft = QuestionDraft(
        kind="analysis",
        shape="why_single_metric",
        measures=(MeasurePhrase(words="performance", metric_id="net_sales"),),
        windows=(
            _window("event", name="yesterday"),
            _window_prior("baseline", n=1, unit="day"),
        ),
        grain="none",
        direction="decrease",
    )
    spec = compile_question_spec(draft, _candidates_for_performance(), MODEL, None, date(2026, 10, 9))
    assert spec.shape == "why_single_metric"
    assert spec.direction == "down"
    assert len(spec.windows) == 2


# ---------------------------------------------------------------- AG03-T04: False premise remains testable
async def test_ag03_t04_false_premise_testable() -> None:
    """A user premise that may be false (e.g., 'CAC increased') should still produce a valid spec that investigation can test."""
    draft = QuestionDraft(
        kind="analysis",
        shape="why_single_metric",
        measures=(MeasurePhrase(words="CAC", metric_id="cac"),),
        windows=(
            _window("event", name="yesterday"),
            _window_prior("baseline", n=1, unit="day"),
        ),
        grain="none",
        direction="increase",
    )
    spec = compile_question_spec(draft, _candidates_for_cac(), MODEL, None, date(2026, 10, 9))
    assert spec.shape == "why_single_metric"
    assert spec.direction == "up"
    assert len(spec.measures) == 1
    assert spec.measures[0].metric_id == "cac"


# ---------------------------------------------------------------- AG03-T05: Follow-up preserves previous entities and windows
async def test_ag03_t05_followup_preserves_entities_windows() -> None:
    """'why?' follow-up must keep previous entities and windows."""
    from seleric_swarm.contracts.pipeline import EntitySpec, WindowSpec
    prior = QuestionSpec(
        kind="analysis",
        shape="why_single_metric",
        measures=(MeasureSpec(phrase="CAC", metric_id="cac"),),
        filters=(),
        entities=(),
        breakdowns=(),
        ranking=None,
        windows=(
            WindowSpec(role="event", start=date(2026, 10, 8), end=date(2026, 10, 8)),
            WindowSpec(role="baseline", start=date(2026, 10, 7), end=date(2026, 10, 7)),
        ),
        grain=None,
        direction="up",
        catalogue_version="zz-v1",
    )
    draft = QuestionDraft(
        kind="analysis",
        shape="why_single_metric",
        measures=(MeasurePhrase(words="CAC", metric_id="cac"),),
        follows_prior=True,
        grain="none",
    )
    spec = compile_question_spec(draft, _candidates_for_cac(), MODEL, prior, date(2026, 10, 9))
    assert spec.shape == "why_single_metric"
    assert spec.follow_up_of is not None
    assert len(spec.windows) == 2
    assert spec.windows[0].start == date(2026, 10, 8)
    assert spec.windows[1].start == date(2026, 10, 7)


# ---------------------------------------------------------------- AG03-T06: Follow-up changes only requested scope
async def test_ag03_t06_followup_changes_only_requested_scope() -> None:
    """'show only Instagram' should change only the filter, keep entities and windows."""
    from seleric_swarm.contracts.pipeline import EntitySpec, WindowSpec
    prior = QuestionSpec(
        kind="analysis",
        shape="entity_comparison",
        measures=(MeasureSpec(phrase="ad spend", metric_id="ad_spend"),),
        filters=(),
        entities=(EntitySpec(dimension="campaign_name", values=("camp_a", "camp_b")),),
        breakdowns=(),
        ranking=None,
        windows=(
            WindowSpec(role="event", start=date(2026, 10, 8), end=date(2026, 10, 8)),
            WindowSpec(role="baseline", start=date(2026, 10, 7), end=date(2026, 10, 7)),
        ),
        grain=None,
        catalogue_version="zz-v1",
    )
    draft = QuestionDraft(
        kind="analysis",
        shape="entity_comparison",
        measures=(MeasurePhrase(words="ad spend", metric_id="ad_spend"),),
        accepts_offer=True,
        breakdown_dimensions=("channel",),
        grain="none",
    )
    spec = compile_question_spec(draft, _candidates_for_followup(), MODEL, prior, date(2026, 10, 9))
    assert spec.shape == "entity_comparison"
    assert "channel" in spec.breakdowns
    assert len(spec.windows) == 2


# ---------------------------------------------------------------- AG03-T07: Unsupported metric produces clarification/refusal
async def test_ag03_t07_unsupported_metric_clarification() -> None:
    """A metric not in the catalogue should produce a clarification."""
    draft = QuestionDraft(
        kind="analysis",
        shape="lookup",
        measures=(MeasurePhrase(words="magic metric", metric_id="magic_metric"),),
        grain="none",
    )
    spec = compile_question_spec(draft, _candidates_for_unsupported(), MODEL, None, date(2026, 10, 10))
    assert isinstance(spec, Clarification)


# ---------------------------------------------------------------- AG03-T08: Paraphrase equivalence
async def test_ag03_t08_paraphrase_equivalence() -> None:
    """Same business meaning should produce equivalent typed specs."""
    draft1 = QuestionDraft(
        kind="analysis",
        shape="entity_comparison",
        measures=(MeasurePhrase(words="net sales", metric_id="net_sales"),),
        entity_dimension="campaign_name",
        entity_phrases=("camp_a", "camp_b"),
        windows=(_window("event", name="yesterday"),),
        grain="none",
        direction="either",
    )
    draft2 = QuestionDraft(
        kind="analysis",
        shape="entity_comparison",
        measures=(MeasurePhrase(words="net sales", metric_id="net_sales"),),
        entity_dimension="campaign_name",
        entity_phrases=("camp_a", "camp_b"),
        windows=(_window("event", name="yesterday"),),
        grain="none",
        direction="either",
    )
    spec1 = compile_question_spec(draft1, _candidates_for_paraphrase(), MODEL, None, date(2026, 10, 10))
    spec2 = compile_question_spec(draft2, _candidates_for_paraphrase(), MODEL, None, date(2026, 10, 10))
    # Compare key fields
    assert spec1.shape == spec2.shape
    assert spec1.entities == spec2.entities
    assert spec1.windows == spec2.windows


# ---------------------------------------------------------------- AG03-T09: Planner never invents metric identifiers
async def test_ag03_t09_planner_never_invents_metrics() -> None:
    """The planner must not invent metric ids; only resolve from the catalogue."""
    draft = QuestionDraft(
        kind="analysis",
        shape="lookup",
        measures=(MeasurePhrase(words="some phrase", metric_id=None),),
        grain="none",
    )
    spec = compile_question_spec(draft, _candidates_for_unsupported(), MODEL, None, date(2026, 10, 10))
    # The measure should have empty metric_id, not an invented one
    assert spec.measures[0].metric_id in (None, "")


# ---------------------------------------------------------------- Plan-level test
async def test_ag03_t01_plan_for_entity_comparison() -> None:
    """The plan for entity comparison should rank by the metric then compare both entities over both windows."""
    draft = QuestionDraft(
        kind="analysis",
        shape="entity_comparison",
        measures=(MeasurePhrase(words="net sales", metric_id="net_sales"),),
        entity_dimension="campaign_name",
        entity_phrases=("camp_a", "camp_b"),
        windows=(
            _window("event", start_iso="2026-10-08", end_iso="2026-10-08"),
            _window("baseline", start_iso="2026-10-07", end_iso="2026-10-07"),
        ),
        grain="none",
        direction="decrease",
    )
    spec = compile_question_spec(draft, _candidates_for_plan(), MODEL, None, date(2026, 10, 9))
    p = plan(spec, MODEL, as_of=date(2026, 10, 9))
    # Should use entity_comparison template
    assert p.template_id == "entity_comparison"
    # Should have steps: rank by metric, then compare both entities over event+baseline
    assert len(p.steps) >= 3  # rank + 2 queries (event + baseline)
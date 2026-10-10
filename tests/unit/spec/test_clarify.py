"""M1.4 — ask instead of guessing, through the public ``compile_question_spec``.

Invented ``zz_*`` catalogue: the triggers must come from the draft, the catalogue's own
resolutions and the read model, never from names.
"""

from __future__ import annotations

from datetime import date

import pytest

from seleric_swarm.contracts.pipeline import (
    Clarification,
    MeasurePhrase,
    QuestionDraft,
    QuestionSpec,
    WindowExpr,
    WindowSlot,
)
from seleric_swarm.semantic import ConceptResolution, SemanticReadModel
from seleric_swarm.spec import SpecCandidates, compile_question_spec

AS_OF = date(2026, 10, 10)


def _model() -> SemanticReadModel:
    return SemanticReadModel.from_payload({
        "catalogue_version": "zz-v1",
        "metrics": [
            {"id": "zz_takings_gross", "label": "Gross takings", "unit": "ZZU", "aggregation": "additive",
             "supported_dimensions": ["zz_zone"]},
            {"id": "zz_takings_net", "label": "Net takings", "unit": "ZZU", "aggregation": "additive",
             "supported_dimensions": ["zz_zone"]},
        ],
        "dimensions": [{"id": "zz_zone"}],
    })


def _event(name: str = "yesterday") -> WindowSlot:
    return WindowSlot(role="event", expr=WindowExpr(name=name), source_span=name)  # type: ignore[arg-type]


_TWO_FIT = {
    "takings": ConceptResolution(kind="ambiguous", metric_id=None, suggestions=("zz_takings_gross", "zz_takings_net")),
}


def _vague_measure() -> QuestionDraft:
    return QuestionDraft(kind="analysis", shape="lookup", measures=(MeasurePhrase(words="takings"),),
                         windows=(_event(),), names_period=True)


def test_a_phrase_that_fits_two_metrics_is_asked_with_their_labels() -> None:
    out = compile_question_spec(_vague_measure(), SpecCandidates(), _model(), None, AS_OF, concepts=_TWO_FIT)
    assert isinstance(out, Clarification) and out.reason == "ambiguous_measure"
    assert out.options == ("Gross takings", "Net takings")
    assert "takings" in out.question_to_user


def test_shadow_records_the_question_and_carries_on() -> None:
    out = compile_question_spec(_vague_measure(), SpecCandidates(), _model(), None, AS_OF,
                                concepts=_TWO_FIT, clarify_mode="shadow")
    assert isinstance(out, QuestionSpec)
    assert "clarify_shadow:ambiguous_measure" in out.assumptions


def test_off_ignores_the_trigger() -> None:
    out = compile_question_spec(_vague_measure(), SpecCandidates(), _model(), None, AS_OF,
                                concepts=_TWO_FIT, clarify_mode="off")
    assert isinstance(out, QuestionSpec) and not any(a.startswith("clarify") for a in out.assumptions)


def test_a_resolved_or_single_suggestion_measure_is_not_asked() -> None:
    one = {"takings": ConceptResolution(kind="resolved", metric_id="zz_takings_net")}
    assert isinstance(compile_question_spec(_vague_measure(), SpecCandidates(), _model(), None, AS_OF,
                                            concepts=one), QuestionSpec)
    single = {"takings": ConceptResolution(kind="ambiguous", suggestions=("zz_takings_net",))}
    assert isinstance(compile_question_spec(_vague_measure(), SpecCandidates(), _model(), None, AS_OF,
                                            concepts=single), QuestionSpec)


def test_a_comparison_with_one_window_asks_for_the_other() -> None:
    draft = QuestionDraft(kind="analysis", shape="period_comparison",
                          measures=(MeasurePhrase(words="net takings", metric_id="zz_takings_net"),),
                          windows=(_event("last_week"),), names_period=True)
    out = compile_question_spec(draft, SpecCandidates(), _model(), None, AS_OF)
    assert isinstance(out, Clarification) and out.reason == "missing_comparison_window"


def test_a_comparison_with_both_windows_is_not_asked() -> None:
    draft = QuestionDraft(kind="analysis", shape="period_comparison",
                          measures=(MeasurePhrase(words="net takings", metric_id="zz_takings_net"),),
                          windows=(_event("this_week"),
                                   WindowSlot(role="baseline", expr=WindowExpr(name="last_week"))),
                          names_period=True)
    assert isinstance(compile_question_spec(draft, SpecCandidates(), _model(), None, AS_OF), QuestionSpec)


@pytest.mark.parametrize("mode", ["enforce", "shadow"])
def test_only_one_question_per_turn(mode) -> None:
    """Several triggers at once: the model's own question wins, and only one is asked/recorded."""
    draft = _vague_measure().model_copy(update={"needs_clarification": "Which store?"})
    out = compile_question_spec(draft, SpecCandidates(), _model(), None, AS_OF, concepts=_TWO_FIT,
                                clarify_mode=mode)
    if mode == "enforce":
        assert isinstance(out, Clarification) and out.reason == "model_asked"
    else:
        assert isinstance(out, QuestionSpec)
        assert [a for a in out.assumptions if a.startswith("clarify")] == ["clarify_shadow:model_asked"]


def test_ambiguous_value_options_are_its_dimensions_regardless_of_mode() -> None:
    from seleric_swarm.spec import ValueHit

    hits = SpecCandidates(values=(
        ValueHit(term="north", dimension="zz_zone", value="north", volume=10),
        ValueHit(term="north", dimension="zz_region", value="north", volume=9),
    ))
    draft = QuestionDraft(kind="analysis", shape="lookup",
                          measures=(MeasurePhrase(words="net takings", metric_id="zz_takings_net"),),
                          windows=(_event(),))
    out = compile_question_spec(draft, hits, _model(), None, AS_OF, clarify_mode="off")
    assert isinstance(out, Clarification) and out.reason == "ambiguous_value"
    assert set(out.options) == {"zz zone", "zz region"}

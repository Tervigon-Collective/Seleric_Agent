"""The draft's catalogue labels map back to ids (exact, unique matches only)."""

from __future__ import annotations

from seleric_swarm.contracts.pipeline import MeasurePhrase, QuestionDraft
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.spec._names import resolve_draft_names

MODEL = SemanticReadModel.from_payload({
    "metrics": [{"id": "zz_take", "label": "Take amount"}, {"id": "zz_a", "label": "Twin"}, {"id": "zz_b", "label": "Twin"}],
    "dimensions": [{"id": "zz_zone", "label": "Sales zone"}],
})


def _draft(**kw) -> QuestionDraft:
    return QuestionDraft(kind="analysis", shape="lookup", **kw)


def test_label_maps_to_id() -> None:
    d = resolve_draft_names(_draft(measures=(MeasurePhrase(words="take", metric_id="Take Amount"),)), MODEL)
    assert d.measures[0].metric_id == "zz_take"


def test_words_used_when_id_missing_and_ambiguous_left_empty() -> None:
    d = resolve_draft_names(_draft(measures=(MeasurePhrase(words="take amount"), MeasurePhrase(words="x", metric_id="Twin"))), MODEL)
    assert [m.metric_id for m in d.measures] == ["zz_take", "Twin"]


def test_dimension_label_maps_to_id() -> None:
    d = resolve_draft_names(_draft(breakdown_dimensions=("Sales zone",), entity_dimension="sales_zone"), MODEL)
    assert d.breakdown_dimensions == ("zz_zone",) and d.entity_dimension == "zz_zone"


def test_later_period_becomes_the_event() -> None:
    from datetime import date

    from seleric_swarm.contracts.pipeline import WindowSpec
    from seleric_swarm.spec._compile import _later_is_event

    early = WindowSpec(role="event", start=date(2026, 8, 1), end=date(2026, 8, 1))
    late = WindowSpec(role="baseline", start=date(2026, 8, 2), end=date(2026, 8, 2))
    out = {w.role: w.start for w in _later_is_event((early, late))}
    assert out == {"event": date(2026, 8, 2), "baseline": date(2026, 8, 1)}

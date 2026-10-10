"""When to ask instead of guess (M1.4).

One place decides whether a turn needs a question back to the user, and asks at most one.
The triggers read only the draft, the catalogue's own resolutions and the read model: no
metric, dimension or value names live here.

``mode`` follows the rollout flags (``CLARIFY_MODE``):
- ``enforce`` returns the ``Clarification``;
- ``shadow`` records it as an assumption on the spec and carries on (so live traffic measures how
  often it would ask);
- ``off`` ignores it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from seleric_swarm.contracts.pipeline import Clarification, QuestionDraft, QuestionSpec, WindowSpec
from seleric_swarm.semantic import ConceptResolution, SemanticReadModel

ClarifyMode = Literal["off", "shadow", "enforce"]

# Shapes whose answer needs both an event and a baseline window.
_TWO_WINDOW_SHAPES = frozenset({"period_comparison"})
_MAX_OPTIONS = 4


def clarification_for(
    draft: QuestionDraft,
    *,
    windows: Sequence[WindowSpec],
    concepts: Mapping[str, ConceptResolution],
    read_model: SemanticReadModel,
    prior: QuestionSpec | None,
) -> Clarification | None:
    """The one question this turn needs, or None. Triggers in order; the first wins."""
    if draft.needs_clarification:
        return Clarification(reason="model_asked", question_to_user=draft.needs_clarification)
    for measure in draft.measures:
        if measure.metric_id:
            continue
        resolution = concepts.get(measure.words)
        if resolution is None or resolution.metric_id or len(resolution.suggestions) < 2:
            continue
        options = tuple(_metric_label(read_model, mid) for mid in resolution.suggestions[:_MAX_OPTIONS])
        return Clarification(
            reason="ambiguous_measure",
            question_to_user=f"By \"{measure.words}\" do you mean {_either(options)}?",
            options=options,
        )
    if draft.shape in _TWO_WINDOW_SHAPES and prior is None:
        roles = {window.role for window in windows}
        if not {"event", "baseline"} <= roles:
            return Clarification(
                reason="missing_comparison_window",
                question_to_user="Which two periods should I compare?",
            )
    return None


def ambiguous_value(term: str, dimensions: Sequence[str], read_model: SemanticReadModel) -> Clarification:
    """A named value recorded on several dimensions with comparable volume."""
    options = tuple(_dimension_label(read_model, dim) for dim in dimensions[:_MAX_OPTIONS])
    return Clarification(
        reason="ambiguous_value",
        question_to_user=f"\"{term}\" appears in more than one place. Do you mean the {_either(options)}?",
        options=options,
    )


def apply_mode(
    mode: ClarifyMode, clarification: Clarification | None, spec: QuestionSpec
) -> QuestionSpec | Clarification:
    """Enforce, record (shadow), or drop the clarification per the rollout flag."""
    if clarification is None or mode == "off":
        return spec
    if mode == "shadow":
        note = f"clarify_shadow:{clarification.reason}"
        return spec.model_copy(update={"assumptions": (*spec.assumptions, note)})
    return clarification


def _metric_label(read_model: SemanticReadModel, metric_id: str) -> str:
    return read_model.label_for(metric_id) or metric_id.replace("_", " ")


def _dimension_label(read_model: SemanticReadModel, dimension: str) -> str:
    del read_model  # dimension labels are not in the read model yet; ids are made readable
    return dimension.replace("_", " ")


def _either(options: Sequence[str]) -> str:
    if len(options) <= 1:
        return "".join(options)
    return ", ".join(options[:-1]) + f" or {options[-1]}"

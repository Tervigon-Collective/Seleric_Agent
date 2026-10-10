"""Compile QuestionDraft → QuestionSpec (pure; no I/O)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from seleric_swarm.agent.scope_model import RequiredScope, RequiredWindow, ValueFilter
from seleric_swarm.contracts.pipeline import (
    Clarification,
    MeasureSpec,
    QuestionDraft,
    QuestionSpec,
    RankingSpec,
)
from seleric_swarm.semantic import ConceptResolution, SemanticReadModel
from seleric_swarm.spec._clarify import ClarifyMode, ambiguous_value, apply_mode, clarification_for
from seleric_swarm.spec._edit import merge_with_prior
from seleric_swarm.spec._filters import (
    SpecCandidates,
    pick_entities_and_filters,
    pick_exclusions,
)
from seleric_swarm.spec._names import resolve_draft_names
from seleric_swarm.spec._validate import validate_question_spec
from seleric_swarm.spec._windows import resolve_window_slots

_DIRECTION = {"increase": "up", "decrease": "down", "either": "either"}


def compile_question_spec(
    draft: QuestionDraft,
    candidates: SpecCandidates,
    read_model: SemanticReadModel,
    prior: QuestionSpec | None,
    as_of: date,
    *,
    concepts: Mapping[str, ConceptResolution] | None = None,
    clarify_mode: ClarifyMode = "enforce",
) -> QuestionSpec | Clarification:
    """Build a validated QuestionSpec from the draft + value candidates + read model.

    ``concepts`` maps a measure's words to the catalogue's resolution of them (used to ask when
    a phrase fits several metrics). ``clarify_mode`` is ``CLARIFY_MODE``: the pipeline passes the
    setting; called directly, the compiler asks (``enforce``).
    """
    draft = resolve_draft_names(draft, read_model)
    if draft.kind == "conversation":
        return Clarification(
            reason="conversation",
            question_to_user=draft.reply or "How can I help with the numbers?",
        )

    entities, filters, ambiguity = pick_entities_and_filters(
        draft=draft, candidates=candidates, read_model=read_model
    )
    if ambiguity:
        # A value that could mean two different things is never guessed (A4), whatever the mode.
        term, dimensions = ambiguity
        return ambiguous_value(term, dimensions, read_model)

    exclusions = pick_exclusions(draft=draft, candidates=candidates)
    filters = (*filters, *exclusions)

    windows = resolve_window_slots(draft.windows, as_of=as_of)
    grain = None if draft.grain == "none" else draft.grain
    breakdowns = tuple(
        dim
        for dim in draft.breakdown_dimensions
        if dim and not read_model.is_time_dimension(dim)
    )
    # Multi-value entity comparison → also break down by that dimension.
    if draft.shape == "entity_comparison" and entities:
        entity_dim = entities[0].dimension
        if entity_dim and entity_dim not in breakdowns:
            breakdowns = (*breakdowns, entity_dim)
    # Several named values on one family for a comparison shape → breakdown too.
    if draft.shape in ("breakdown", "entity_comparison", "composition"):
        for filt in filters:
            if filt.operator == "in" and len(filt.values) > 1 and filt.dimension not in breakdowns:
                breakdowns = (*breakdowns, filt.dimension)

    measures = tuple(
        MeasureSpec(phrase=item.words, metric_id=item.metric_id, axes=item.axes)
        for item in draft.measures
    )
    if not measures and draft.rank_by is not None:
        measures = (
            MeasureSpec(
                phrase=draft.rank_by.words,
                metric_id=draft.rank_by.metric_id,
                axes=draft.rank_by.axes,
            ),
        )

    ranking: RankingSpec | None = None
    if draft.rank_by is not None and draft.rank_by.metric_id:
        ranking = RankingSpec(
            by_metric_id=draft.rank_by.metric_id,
            order=draft.rank_order,
            limit=int(draft.top_n or 5),
        )

    through_hour = draft.through_hour
    if through_hour is not None and windows:
        windows = tuple(
            window.model_copy(update={"through_hour": through_hour})
            if window.role == "event"
            else window
            for window in windows
        )

    assumptions: list[str] = []
    if candidates.axes:
        assumptions.append("axes:" + ",".join(f"{k}={v}" for k, v in candidates.axes))

    fresh = QuestionSpec(
        kind=draft.kind if draft.kind != "exploration" else "exploration",
        shape=draft.shape,
        measures=measures,
        filters=filters,
        entities=entities,
        breakdowns=breakdowns,
        windows=windows,
        grain=grain,
        ranking=ranking,
        direction=_DIRECTION.get(draft.direction, "either"),  # type: ignore[arg-type]
        follow_up_of=None,
        assumptions=tuple(assumptions),
        catalogue_version=read_model.version or "",
    )

    if prior is not None and (draft.follows_prior or draft.accepts_offer or draft.restyles_prior):
        fresh = fresh.model_copy(update={"follow_up_of": prior.follow_up_of or "prior"})
        fresh = merge_with_prior(fresh, prior, draft)

    checked = validate_question_spec(fresh, read_model=read_model)
    if isinstance(checked, Clarification):
        return checked
    question = clarification_for(
        draft, windows=checked.windows, concepts=concepts or {}, read_model=read_model, prior=prior
    )
    return apply_mode(clarify_mode, question, checked)


def scope_from_spec(spec: QuestionSpec) -> RequiredScope:
    """Build RequiredScope once from the typed spec (replaces runner mutations)."""
    breakdowns = frozenset(frozenset({dim}) for dim in spec.breakdowns if dim)
    value_filters: list[ValueFilter] = []
    for filt in spec.filters:
        if filt.operator != "in":
            continue
        value_filters.append(
            ValueFilter(
                term=filt.source_span or ",".join(filt.values),
                dimensions=frozenset({filt.dimension}),
                values=tuple(filt.values),
            )
        )
    for entity in spec.entities:
        value_filters.append(
            ValueFilter(
                term=entity.source_span or ",".join(entity.values),
                dimensions=frozenset({entity.dimension}),
                values=tuple(entity.values),
            )
        )
    windows = tuple(
        RequiredWindow(start=window.start, end=window.end)
        for window in spec.windows
        if window.role in ("event", "baseline")
    )
    return RequiredScope(
        breakdowns=breakdowns,
        value_filters=tuple(value_filters),
        temporal_grain=spec.grain,
        windows=windows,
    )

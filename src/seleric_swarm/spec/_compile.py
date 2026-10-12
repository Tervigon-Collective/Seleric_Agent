"""Compile QuestionDraft → QuestionSpec (pure; no I/O)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

from seleric_swarm.agent.scope_model import RequiredScope, RequiredWindow, ValueFilter
from seleric_swarm.contracts.pipeline import (
    Clarification,
    MeasureSpec,
    QuestionDraft,
    QuestionSpec,
    RankingSpec,
    WindowSpec,
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
_FAMILY_SHAPES = frozenset({"entity_comparison", "breakdown", "ranking", "composition"})


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

    windows = _later_is_event(resolve_window_slots(draft.windows, as_of=as_of))
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
    # A comparative question that names the family but no values ("the worst campaign",
    # "product sales"): keep the family dimension as the leading breakdown so the planner
    # slices the whole family instead of silently degrading to whole-account totals.
    if draft.shape in _FAMILY_SHAPES and not entities:
        entity_dim = (draft.entity_dimension or "").strip()
        if (
            entity_dim
            and not read_model.is_time_dimension(entity_dim)
            and entity_dim not in breakdowns
        ):
            breakdowns = (entity_dim, *breakdowns)
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

    fresh = _drop_uncarried_breakdowns(fresh, read_model)
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


def _later_is_event(windows: tuple[WindowSpec, ...]) -> tuple[WindowSpec, ...]:
    """Two explicit periods compared: the later one is the event. The model sometimes labels them
    in the order the user wrote them ("Aug 2 vs Aug 1"); swap the roles instead of refusing."""
    by_role = {w.role: w for w in windows}
    event, baseline = by_role.get("event"), by_role.get("baseline")
    if event is None or baseline is None or baseline.end < event.start:
        return windows
    if baseline.start < event.start:
        # The baseline runs into the event ("last 3 days" read as the baseline of "yesterday"):
        # it ends the day before the event starts.
        clipped = baseline.model_copy(update={"end": event.start - timedelta(days=1)})
        return tuple(clipped if w is baseline else w for w in windows)
    swapped = {"event": "baseline", "baseline": "event"}
    return tuple(w.model_copy(update={"role": swapped.get(w.role, w.role)}) for w in windows)


def _drop_uncarried_breakdowns(spec: QuestionSpec, read_model: SemanticReadModel) -> QuestionSpec:
    """A breakdown no asked measure can be sliced by (often carried over from the previous turn)
    is dropped and stated as an assumption, instead of failing the whole question."""
    ids = [m.metric_id for m in spec.measures if m.metric_id and read_model.has_metric(m.metric_id)]
    if not ids or not spec.breakdowns:
        return spec

    def carried(mid: str, dim: str) -> bool:
        return read_model.carries(mid, dim) or read_model.conformed_sibling(
            dim, set(read_model.supported_dimensions_for(mid) or ())) is not None

    keep = tuple(d for d in spec.breakdowns if all(carried(mid, d) for mid in ids))
    dropped = [d for d in spec.breakdowns if d not in keep]
    if not dropped:
        return spec
    return spec.model_copy(update={"breakdowns": keep, "assumptions": (
        *spec.assumptions, *(f"dropped_breakdown:{d}" for d in dropped))})

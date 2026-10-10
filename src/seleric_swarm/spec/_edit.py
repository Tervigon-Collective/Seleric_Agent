"""Follow-up edits: only fields the new draft sets replace the prior spec."""

from __future__ import annotations

from seleric_swarm.contracts.pipeline import QuestionDraft, QuestionSpec


def merge_with_prior(
    fresh: QuestionSpec,
    prior: QuestionSpec,
    draft: QuestionDraft,
) -> QuestionSpec:
    """Keep prior windows/entities/filters/measures when the new draft left them empty."""
    windows = fresh.windows if fresh.windows else prior.windows
    entities = fresh.entities if fresh.entities else prior.entities
    filters = fresh.filters if fresh.filters else prior.filters
    measures = fresh.measures if fresh.measures else prior.measures
    breakdowns = fresh.breakdowns if fresh.breakdowns else prior.breakdowns
    grain = fresh.grain if fresh.grain is not None else prior.grain
    ranking = fresh.ranking if fresh.ranking is not None else prior.ranking

    if draft.follows_prior or draft.accepts_offer:
        if not fresh.windows:
            windows = prior.windows
        if not fresh.entities:
            entities = prior.entities
        if not fresh.filters:
            filters = prior.filters

    return fresh.model_copy(
        update={
            "windows": windows,
            "entities": entities,
            "filters": filters,
            "measures": measures,
            "breakdowns": breakdowns,
            "grain": grain,
            "ranking": ranking,
            "follow_up_of": prior.follow_up_of or fresh.follow_up_of,
            "assumptions": tuple(dict.fromkeys([*prior.assumptions, *fresh.assumptions])),
            "catalogue_version": fresh.catalogue_version or prior.catalogue_version,
        }
    )

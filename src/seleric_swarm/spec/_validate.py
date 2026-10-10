"""Validate a compiled QuestionSpec against the semantic read model."""

from __future__ import annotations

from seleric_swarm.contracts.pipeline import Clarification, QuestionSpec
from seleric_swarm.semantic import SemanticReadModel


def validate_question_spec(
    spec: QuestionSpec,
    *,
    read_model: SemanticReadModel,
) -> QuestionSpec | Clarification:
    """Check ids, carries, and window order. Returns Clarification on hard failures."""
    problems: list[str] = []

    known_metrics = read_model.metric_ids()
    for measure in spec.measures:
        mid = measure.metric_id
        if mid and known_metrics and mid not in known_metrics:
            problems.append(f"unknown metric id: {mid}")

    dim_ids = frozenset(dim.id for dim in read_model.dimensions)
    for breakdown in spec.breakdowns:
        if dim_ids and breakdown not in dim_ids:
            problems.append(f"unknown breakdown dimension: {breakdown}")
            continue
        for measure in spec.measures:
            mid = measure.metric_id
            if not mid:
                continue
            if not read_model.carries(mid, breakdown):
                sibling = read_model.conformed_sibling(
                    breakdown, set(read_model.supported_dimensions_for(mid) or ())
                )
                if sibling is None:
                    problems.append(f"metric {mid} does not carry breakdown {breakdown}")

    for filt in spec.filters:
        if dim_ids and filt.dimension not in dim_ids:
            problems.append(f"unknown filter dimension: {filt.dimension}")
            continue
        for measure in spec.measures:
            mid = measure.metric_id
            if not mid:
                continue
            if not read_model.carries(mid, filt.dimension):
                sibling = read_model.conformed_sibling(
                    filt.dimension,
                    set(read_model.supported_dimensions_for(mid) or ()),
                    list(filt.values),
                )
                if sibling is None:
                    problems.append(f"metric {mid} does not carry filter {filt.dimension}")

    for entity in spec.entities:
        if dim_ids and entity.dimension not in dim_ids:
            problems.append(f"unknown entity dimension: {entity.dimension}")

    event = next((window for window in spec.windows if window.role == "event"), None)
    baseline = next((window for window in spec.windows if window.role == "baseline"), None)
    if event and baseline and baseline.end >= event.start:
        problems.append(
            f"baseline {baseline.start}..{baseline.end} must end before event "
            f"{event.start}..{event.end}"
        )

    if not problems:
        return spec

    # Unknown ids / carry failures → ask; window order is a hard clarify.
    return Clarification(
        reason="invalid_spec",
        question_to_user=problems[0],
        options=(),
    )

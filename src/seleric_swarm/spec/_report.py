"""Compile QuestionDraft (+ objectives) → ReportSpec (AG08-01)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from seleric_swarm.contracts.pipeline import (
    Clarification,
    MeasurePhrase,
    MeasureSpec,
    ObjectiveDraft,
    QuestionDraft,
    QuestionSpec,
    RankingSpec,
)
from seleric_swarm.contracts.pipeline._report import ReportSpec, SectionSpec
from seleric_swarm.plan._report import single_section_report_from_spec
from seleric_swarm.semantic import ConceptResolution, SemanticReadModel
from seleric_swarm.spec._clarify import ClarifyMode
from seleric_swarm.spec._compile import compile_question_spec
from seleric_swarm.spec._filters import SpecCandidates


def _intent(shape: str, explicit: str | None = None) -> str:
    if explicit and explicit != "narrative":
        return explicit
    return {
        "funnel": "funnel",
        "trend": "trend",
        "breakdown": "table",
        "entity_comparison": "ranking",
        "ranking": "ranking",
        "lookup": "kpis",
        "composition": "table",
        "period_comparison": "kpis",
    }.get(shape, "narrative")


def _section_from_objective(
    index: int,
    objective: ObjectiveDraft,
    shared: QuestionSpec,
    read_model: SemanticReadModel,
) -> SectionSpec:
    measures = objective.measures or ()
    metric_ids = tuple(
        m.metric_id for m in measures if m.metric_id and read_model.has_metric(m.metric_id)
    )
    candidates = tuple(m.words for m in measures) or tuple(
        m.phrase for m in shared.measures
    )
    if not metric_ids and shared.measures:
        # Fall back to shared resolved ids when the objective only carries words.
        metric_ids = tuple(m.metric_id for m in shared.measures if m.metric_id)

    ranking = None
    criterion_status: str = "unspecified"
    ranking_label = ""
    if objective.rank_by is not None and objective.rank_by.metric_id:
        ranking = RankingSpec(
            by_metric_id=objective.rank_by.metric_id,
            order=objective.rank_order,
            limit=int(objective.top_n or 5),
        )
        criterion_status = "explicit"
        ranking_label = objective.rank_by.words or objective.rank_by.metric_id
    elif objective.shape in ("ranking", "entity_comparison") and metric_ids:
        ranking = RankingSpec(
            by_metric_id=metric_ids[0],
            order=objective.rank_order,
            limit=int(objective.top_n or 5),
        )
        criterion_status = "assumed"
        ranking_label = read_model.label_for(metric_ids[0]) or metric_ids[0]

    dims = tuple(d for d in objective.breakdown_dimensions if d)
    if objective.entity_dimension and objective.entity_dimension not in dims:
        dims = (objective.entity_dimension, *dims)
    if not dims and shared.breakdowns and objective.shape in (
        "ranking",
        "entity_comparison",
        "breakdown",
    ):
        dims = shared.breakdowns

    grain = None if objective.grain == "none" else objective.grain
    return SectionSpec(
        section_id=f"s{index}",
        objective=objective.objective or objective.shape,
        shape=objective.shape if objective.shape != "other" else shared.shape,
        measure_candidates=candidates,
        metric_ids=metric_ids,
        dimensions=dims,
        time_basis=None,
        population=None,
        filters=(),
        grain=grain or shared.grain,
        ranking=ranking,
        criterion_status=criterion_status,  # type: ignore[arg-type]
        ranking_label=ranking_label,
        required_evidence=metric_ids,
        presentation_intent=_intent(objective.shape, objective.presentation_intent),  # type: ignore[arg-type]
    )


def compile_report_spec(
    draft: QuestionDraft,
    candidates: SpecCandidates,
    read_model: SemanticReadModel,
    prior: QuestionSpec | None,
    as_of: date,
    *,
    concepts: Mapping[str, ConceptResolution] | None = None,
    clarify_mode: ClarifyMode = "enforce",
) -> ReportSpec | Clarification | QuestionSpec:
    """Compile a ReportSpec when the draft has objectives; else a QuestionSpec (legacy path).

    Returns Clarification on invalid drafts. Callers that only want ReportSpec should
    wrap a QuestionSpec via ``single_section_report_from_spec``.
    """
    base = compile_question_spec(
        draft,
        candidates,
        read_model,
        prior,
        as_of,
        concepts=concepts,
        clarify_mode=clarify_mode,
    )
    if isinstance(base, Clarification):
        return base

    if not draft.objectives:
        return base

    sections = tuple(
        _section_from_objective(i, obj, base, read_model)
        for i, obj in enumerate(draft.objectives, start=1)
    )
    # Ensure at least the shared measures appear if an objective resolved empty.
    repaired: list[SectionSpec] = []
    for section in sections:
        if section.metric_ids:
            repaired.append(section)
            continue
        repaired.append(
            section.model_copy(
                update={
                    "metric_ids": tuple(m.metric_id for m in base.measures if m.metric_id),
                    "measure_candidates": section.measure_candidates
                    or tuple(m.phrase for m in base.measures),
                    "required_evidence": tuple(m.metric_id for m in base.measures if m.metric_id),
                }
            )
        )
    return ReportSpec(
        windows=base.windows,
        filters=base.filters,
        entities=base.entities,
        sections=tuple(repaired),
        catalogue_version=base.catalogue_version,
        follow_up_of=base.follow_up_of,
        assumptions=base.assumptions,
    )


def as_report_spec(spec: QuestionSpec | ReportSpec) -> ReportSpec:
    if isinstance(spec, ReportSpec):
        return spec
    return single_section_report_from_spec(spec)

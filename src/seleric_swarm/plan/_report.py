"""Multi-section report planning (AG08-01).

Plans each SectionSpec independently with the existing template registry, then
namespaces step ids (``s1.q1``) so a single ReportPlan can carry several templates.
A section whose template declines is marked unsupported; it does not sink the mission.
"""

from __future__ import annotations

from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    DeriveStep,
    EngineStep,
    QueryStep,
    QuestionSpec,
    RankingSpec,
)
from seleric_swarm.contracts.pipeline._report import (
    ReportPlan,
    ReportSpec,
    SectionPlan,
    SectionSpec,
)
from seleric_swarm.plan._registry import AGENT_TEMPLATE_ID, plan
from seleric_swarm.semantic import SemanticReadModel


def _namespace_steps(plan_: AnalysisPlan, prefix: str) -> AnalysisPlan:
    """Rewrite step ids and cross-step refs under ``prefix`` (e.g. ``s1``)."""
    mapping = {step.step_id: f"{prefix}.{step.step_id}" for step in plan_.steps}
    out: list[QueryStep | EngineStep | DeriveStep] = []
    for step in plan_.steps:
        sid = mapping[step.step_id]
        if isinstance(step, QueryStep):
            out.append(
                step.model_copy(
                    update={
                        "step_id": sid,
                        "entities_from_step": (
                            mapping.get(step.entities_from_step, step.entities_from_step)
                            if step.entities_from_step
                            else None
                        ),
                    }
                )
            )
        elif isinstance(step, DeriveStep):
            inputs = []
            for raw in step.inputs:
                if isinstance(raw, str):
                    inputs.append(mapping.get(raw, raw))
                else:
                    inputs.append(raw.model_copy(update={"id": mapping.get(raw.id, raw.id)}))
            out.append(step.model_copy(update={"step_id": sid, "inputs": tuple(inputs)}))
        else:
            out.append(
                step.model_copy(
                    update={
                        "step_id": sid,
                        "inputs": tuple(mapping.get(i, i) for i in step.inputs),
                    }
                )
            )
    return plan_.model_copy(update={"steps": tuple(out)})


def _section_as_question(
    report: ReportSpec,
    section: SectionSpec,
    *,
    kind: str = "analysis",
) -> QuestionSpec:
    """Project one section onto a QuestionSpec the existing templates understand."""
    from seleric_swarm.contracts.pipeline import MeasureSpec

    measures = tuple(
        MeasureSpec(phrase=mid, metric_id=mid) for mid in section.metric_ids
    ) or tuple(
        MeasureSpec(phrase=p, metric_id=None) for p in section.measure_candidates
    )
    ranking = section.ranking
    # Exploration / overview keep their kind so ExplorationTemplate still matches.
    resolved_kind: str = "exploration" if kind == "exploration" else "analysis"
    return QuestionSpec(
        kind=resolved_kind,  # type: ignore[arg-type]
        shape=section.shape,
        measures=measures,
        filters=(*report.filters, *section.filters),
        entities=tuple(report.entities),
        breakdowns=section.dimensions,
        windows=report.windows,
        grain=section.grain,
        ranking=ranking,
        catalogue_version=report.catalogue_version,
        follow_up_of=report.follow_up_of,
        assumptions=report.assumptions,
    )


def plan_report(
    report: ReportSpec,
    read_model: SemanticReadModel,
    *,
    as_of=None,
    headline_metric_ids: tuple[str, ...] = (),
    kind: str = "analysis",
) -> ReportPlan:
    """Build a ReportPlan: one namespaced AnalysisPlan per section."""
    sections: list[SectionPlan] = []
    multi = len(report.sections) > 1
    for index, section in enumerate(report.sections, start=1):
        prefix = f"s{index}"
        spec = _section_as_question(report, section, kind=kind)
        built = plan(
            spec,
            read_model,
            as_of=as_of,
            headline_metric_ids=headline_metric_ids,
        )
        if built.needs_agent or built.template_id == AGENT_TEMPLATE_ID:
            sections.append(
                SectionPlan(
                    section_id=section.section_id,
                    objective=section.objective,
                    template_id=AGENT_TEMPLATE_ID,
                    plan=AnalysisPlan(template_id=AGENT_TEMPLATE_ID, steps=(), needs_agent=True),
                    presentation_intent=section.presentation_intent,
                    criterion_status=section.criterion_status,
                    ranking_label=section.ranking_label,
                    needs_agent=True,
                    unsupported_reason="no_template",
                )
            )
            continue
        # Single-section reports keep bare step ids (q1/d1) so existing answer
        # claims and golden tests still resolve. Multi-section reports namespace.
        planned = _namespace_steps(built, prefix) if multi else built
        sections.append(
            SectionPlan(
                section_id=section.section_id,
                objective=section.objective,
                template_id=built.template_id,
                plan=planned,
                presentation_intent=section.presentation_intent,
                criterion_status=section.criterion_status,
                ranking_label=section.ranking_label,
            )
        )
    return ReportPlan(
        sections=tuple(sections),
        windows=report.windows,
        filters=report.filters,
        needs_agent=bool(sections) and all(s.needs_agent for s in sections),
    )


def report_as_flat_plan(report_plan: ReportPlan) -> AnalysisPlan:
    """Flatten all section steps into one AnalysisPlan for the existing executor."""
    steps: list[QueryStep | EngineStep | DeriveStep] = []
    template_ids: list[str] = []
    for section in report_plan.sections:
        if section.needs_agent:
            continue
        steps.extend(section.plan.steps)
        template_ids.append(section.template_id)
    if not steps:
        return AnalysisPlan(template_id=AGENT_TEMPLATE_ID, steps=(), needs_agent=True)
    return AnalysisPlan(
        template_id="+".join(dict.fromkeys(template_ids)) or "report",
        steps=tuple(steps),
        trusted=True,
    )


def single_section_report_from_spec(spec: QuestionSpec) -> ReportSpec:
    """Wrap a legacy QuestionSpec as a one-section ReportSpec (lookup/trend regression path)."""
    shape = spec.shape if spec.kind != "exploration" else "other"
    section = SectionSpec(
        section_id="s1",
        objective=" ".join(m.phrase for m in spec.measures) or spec.shape,
        shape=shape,
        measure_candidates=tuple(m.phrase for m in spec.measures),
        metric_ids=tuple(m.metric_id for m in spec.measures if m.metric_id),
        dimensions=spec.breakdowns,
        filters=(),
        grain=spec.grain,
        ranking=spec.ranking,
        criterion_status="explicit" if spec.ranking else "unspecified",
        presentation_intent=_intent_for_shape(spec.shape if spec.kind != "exploration" else "lookup"),
        required_evidence=tuple(m.metric_id for m in spec.measures if m.metric_id),
    )
    return ReportSpec(
        windows=spec.windows,
        filters=spec.filters,
        entities=spec.entities,
        sections=(section,),
        catalogue_version=spec.catalogue_version,
        follow_up_of=spec.follow_up_of,
        assumptions=spec.assumptions,
    )


def _intent_for_shape(shape: str) -> str:
    return {
        "funnel": "funnel",
        "trend": "trend",
        "breakdown": "table",
        "entity_comparison": "ranking",
        "lookup": "kpis",
        "composition": "table",
        "ranking": "ranking",
    }.get(shape, "narrative")

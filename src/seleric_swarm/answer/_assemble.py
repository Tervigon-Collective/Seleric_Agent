"""Deterministic report assembler (AG08-05 / AG08-07).

ResultSets + Derivations + Semantic Metadata → Validated Report Blocks.
The LLM never manufactures numbers or chart points.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seleric_swarm.contracts.pipeline import (
    CoverageBlock,
    Derivation,
    FunnelChartBlock,
    Insight,
    InsightBlock,
    KpiCardBlock,
    KpiGridBlock,
    RankingChartBlock,
    RankingTableBlock,
    Ref,
    ResultSet,
    TimeSeriesChartBlock,
)
from seleric_swarm.contracts.pipeline._report import ReportCoverage, ReportPlan, SectionPlan
from seleric_swarm.pipeline._run_plan import PlanRun
from seleric_swarm.semantic import SemanticReadModel

ReportBlock = (
    KpiCardBlock
    | KpiGridBlock
    | TimeSeriesChartBlock
    | FunnelChartBlock
    | RankingChartBlock
    | RankingTableBlock
    | InsightBlock
    | CoverageBlock
)


@dataclass
class ReportSkeleton:
    blocks: list[ReportBlock] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    status: str = "partial"
    limitations: list[str] = field(default_factory=list)


def _section_sets(section: SectionPlan, run: PlanRun) -> list[ResultSet]:
    ids = {s.step_id for s in section.plan.steps}
    return [rs for rs in run.result_sets if rs.step_id in ids and rs.error is None]


def _section_derivations(section: SectionPlan, run: PlanRun) -> list[Derivation]:
    ids = {s.step_id for s in section.plan.steps}
    return [d for d in run.derivations if d.ref.id in ids and d.error is None]


def _kpi_cards(section: SectionPlan, sets: list[ResultSet], read_model: SemanticReadModel) -> list[KpiCardBlock]:
    cards: list[KpiCardBlock] = []
    for rs in sets:
        if rs.breakdowns or len(rs.rows) != 1:
            continue
        cards.append(
            KpiCardBlock(
                ref=rs.ref,
                label=read_model.label_for(rs.metric_id) or rs.metric_id,
                unit=rs.unit,
                population=read_model.grain_for(rs.metric_id) or "",
                time_basis=f"{rs.window.start}..{rs.window.end}",
                provenance=read_model.view_for(rs.metric_id) or "",
                section_id=section.section_id,
            )
        )
    return cards


def _funnel_block(section: SectionPlan, derivations: list[Derivation]) -> FunnelChartBlock | None:
    for d in derivations:
        if d.op != "funnel_stages":
            continue
        stages = []
        for row in d.rows:
            if row.dimensions.get("metric"):
                continue
            stages.append(row.dimensions.get("stage") or "")
        return FunnelChartBlock(
            refs=(d.ref,),
            stage_labels=tuple(s for s in stages if s),
            title=section.objective or "Funnel",
            section_id=section.section_id,
            disclosures=tuple(str(x) for x in (d.params.get("disclosures") or [])),
        )
    return None


def _ranking_blocks(
    section: SectionPlan, sets: list[ResultSet], derivations: list[Derivation], read_model: SemanticReadModel
) -> list[ReportBlock]:
    blocks: list[ReportBlock] = []
    for d in derivations:
        if d.op != "rank":
            continue
            # ranking chart from derivation rows
        criterion = str(d.params.get("criterion") or d.params.get("metric_id") or "rank")
        blocks.append(
            RankingChartBlock(
                ref=d.ref,
                title=f"Top by {criterion}",
                criterion_label=criterion,
                section_id=section.section_id,
            )
        )
    for rs in sets:
        if not rs.breakdowns or not rs.rows:
            continue
        if section.presentation_intent not in ("ranking", "table") and section.template_id not in (
            "ranking",
            "entity_comparison",
            "breakdown",
        ):
            continue
        label = section.ranking_label or read_model.label_for(rs.metric_id) or rs.metric_id
        blocks.append(
            RankingTableBlock(
                result_set=rs.ref,
                columns=(*rs.breakdowns, "value"),
                caption=f"Ranked by {label}",
                criterion_label=label,
                section_id=section.section_id,
            )
        )
        blocks.append(
            RankingChartBlock(
                ref=rs.ref,
                title=f"Top by {label}",
                criterion_label=label,
                name_key=rs.breakdowns[-1] if rs.breakdowns else "",
                id_key=rs.breakdowns[0] if rs.breakdowns else "",
                section_id=section.section_id,
            )
        )
    return blocks


def _trend_block(section: SectionPlan, sets: list[ResultSet]) -> TimeSeriesChartBlock | None:
    refs = []
    for rs in sets:
        if "period_start" in rs.breakdowns or rs.rows and any("period_start" in r.dimensions for r in rs.rows):
            refs.append(rs.ref)
    if not refs:
        return None
    return TimeSeriesChartBlock(
        refs=tuple(refs),
        title=section.objective or "Trend",
        section_id=section.section_id,
    )


def _insights_for_section(
    section: SectionPlan,
    sets: list[ResultSet],
    derivations: list[Derivation],
    read_model: SemanticReadModel,
) -> list[Insight]:
    insights: list[Insight] = []
    for d in derivations:
        if d.op == "funnel_stages":
            leak = d.params.get("largest_leakage")
            stage = d.params.get("largest_leakage_stage") or ""
            if isinstance(leak, (int, float)):
                insights.append(
                    Insight(
                        insight_id=f"{section.section_id}-leak",
                        movement=f"Largest supported drop-off at {stage}",
                        supporting_refs=(d.ref,),
                        segment=str(stage),
                        contribution=float(leak),
                        explanation_kind="mathematical",
                        limitation="Stage ratios only for compatible populations",
                        next_check="Inspect filters and traffic sources on the leaky stage",
                        section_id=section.section_id,
                    )
                )
        if d.op.startswith("finding:"):
            insights.append(
                Insight(
                    insight_id=d.ref.id,
                    movement=str(d.params.get("subject") or d.op),
                    supporting_refs=(d.ref,),
                    explanation_kind="causal" if d.op == "finding:driver" else "descriptive",
                    limitation=str(d.params.get("detail") or ""),
                    next_check="Validate against an alternate window",
                    section_id=section.section_id,
                )
            )
    # Spend concentration: top row share of total when we have a ranked set.
    for rs in sets:
        if len(rs.rows) < 2 or not rs.breakdowns:
            continue
        values = [r.value for r in rs.rows if r.value is not None]
        if not values:
            continue
        total = sum(values)
        if total == 0:
            continue
        top = max(values, key=abs)
        share = abs(top) / abs(total)
        if share >= 0.25:
            insights.append(
                Insight(
                    insight_id=f"{section.section_id}-conc-{rs.metric_id}",
                    movement=f"Top entity holds {share:.0%} of {read_model.label_for(rs.metric_id) or rs.metric_id}",
                    supporting_refs=(rs.ref,),
                    contribution=share,
                    explanation_kind="mathematical",
                    limitation="Share of returned rows only; null-identity amounts excluded from named ranks",
                    next_check="Check unnamed/null identity reconciliation before scaling",
                    section_id=section.section_id,
                )
            )
    return insights[:5]


def assemble(
    report_plan: ReportPlan,
    run: PlanRun,
    coverage: ReportCoverage,
    read_model: SemanticReadModel,
) -> ReportSkeleton:
    blocks: list[ReportBlock] = []
    insights: list[Insight] = []
    limitations = list(coverage.limitations)

    for section in report_plan.sections:
        sets = _section_sets(section, run)
        derivations = _section_derivations(section, run)
        cov = next((c for c in coverage.sections if c.section_id == section.section_id), None)

        cards = _kpi_cards(section, sets, read_model)
        if cards and section.presentation_intent in ("kpis", "narrative", "funnel", "trend"):
            blocks.append(
                KpiGridBlock(
                    cards=tuple(cards),
                    title=section.objective or "KPIs",
                    section_id=section.section_id,
                )
            )

        funnel = _funnel_block(section, derivations)
        if funnel:
            blocks.append(funnel)

        if section.presentation_intent == "trend" or section.template_id == "trend":
            trend = _trend_block(section, sets)
            if trend:
                blocks.append(trend)

        blocks.extend(_ranking_blocks(section, sets, derivations, read_model))

        section_insights = _insights_for_section(section, sets, derivations, read_model)
        insights.extend(section_insights)
        if section_insights and section.presentation_intent in ("narrative", "funnel", "ranking"):
            blocks.append(
                InsightBlock(
                    insights=tuple(section_insights),
                    title="Top learnings",
                    section_id=section.section_id,
                )
            )

        if cov is not None and cov.status != "complete":
            blocks.append(
                CoverageBlock(
                    status=cov.status,
                    notes=cov.notes,
                    section_id=section.section_id,
                )
            )

    # Overall coverage footer.
    blocks.append(
        CoverageBlock(
            status=coverage.status,
            notes=tuple(coverage.limitations[:8]),
            section_id="",
        )
    )

    status = "completed" if coverage.status == "complete" else (
        "failed" if coverage.status in ("unsupported", "unavailable", "query_failed") and not any(
            isinstance(b, (KpiGridBlock, FunnelChartBlock, RankingChartBlock, RankingTableBlock, TimeSeriesChartBlock))
            for b in blocks
        ) else "partial"
    )
    return ReportSkeleton(
        blocks=blocks,
        insights=insights,
        status=status,
        limitations=limitations,
    )

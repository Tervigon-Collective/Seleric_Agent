"""AG08-01 / AG08-02: multi-objective ReportPlan and coverage."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from seleric_swarm.contracts.pipeline import (
    MeasurePhrase,
    MeasureSpec,
    ObjectiveDraft,
    QuestionDraft,
    QuestionSpec,
    RankingSpec,
    WindowExpr,
    WindowSlot,
    WindowSpec,
)
from seleric_swarm.contracts.pipeline._report import ReportSpec, SectionSpec
from seleric_swarm.plan._report import plan_report, report_as_flat_plan, single_section_report_from_spec
from seleric_swarm.pipeline._acquire import AcquireStats, build_report_coverage, section_coverage
from seleric_swarm.pipeline._run_plan import PlanRun
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload(
    {
        "metrics": [
            {"id": "sessions", "aggregation": "additive", "unit": "count", "grain": "session", "view": "web_sessions", "supported_dimensions": ["campaign"]},
            {"id": "ad_spend", "aggregation": "additive", "unit": "INR", "grain": "ad_day", "view": "paid_media", "category": "paid_media", "supported_dimensions": ["campaign", "campaign_id"]},
            {"id": "net_profit", "aggregation": "additive", "unit": "INR", "grain": "brand_order_day_channel_ad", "view": "order_pnl", "category": "profit", "supported_dimensions": ["campaign", "campaign_id"]},
            {"id": "checkouts", "aggregation": "additive", "unit": "count", "grain": "session", "view": "web_sessions", "supported_dimensions": []},
        ],
        "dimensions": [
            {"id": "campaign", "stable_key": "campaign_id"},
            {"id": "campaign_id"},
        ],
    }
)

WINDOW = WindowSpec(role="event", start=date(2026, 10, 1), end=date(2026, 10, 11))


def test_single_section_wraps_lookup() -> None:
    spec = QuestionSpec(
        kind="analysis",
        shape="lookup",
        measures=(MeasureSpec(phrase="ad spend", metric_id="ad_spend"),),
        filters=(),
        entities=(),
        breakdowns=(),
        windows=(WINDOW,),
        grain=None,
        ranking=None,
        catalogue_version="t",
    )
    report = single_section_report_from_spec(spec)
    assert len(report.sections) == 1
    assert report.sections[0].metric_ids == ("ad_spend",)
    plan = plan_report(report, MODEL, as_of=date(2026, 10, 12))
    flat = report_as_flat_plan(plan)
    assert flat.template_id == "lookup"
    assert not flat.needs_agent


def test_multi_section_uses_multiple_templates() -> None:
    report = ReportSpec(
        windows=(WINDOW,),
        filters=(),
        entities=(),
        catalogue_version="t",
        sections=(
            SectionSpec(
                section_id="s1",
                objective="Full funnel diagnostic",
                shape="funnel",
                metric_ids=("sessions", "checkouts"),
                presentation_intent="funnel",
                required_evidence=("sessions", "checkouts"),
            ),
            SectionSpec(
                section_id="s2",
                objective="Meta top performers by spend",
                shape="ranking",
                metric_ids=("ad_spend",),
                dimensions=("campaign",),
                ranking=RankingSpec(by_metric_id="ad_spend", order="desc", limit=5),
                criterion_status="explicit",
                ranking_label="ad spend",
                presentation_intent="ranking",
                required_evidence=("ad_spend",),
            ),
            SectionSpec(
                section_id="s3",
                objective="Net profit performers",
                shape="ranking",
                metric_ids=("net_profit",),
                dimensions=("campaign",),
                ranking=RankingSpec(by_metric_id="net_profit", order="desc", limit=5),
                criterion_status="explicit",
                ranking_label="net profit",
                presentation_intent="ranking",
                required_evidence=("net_profit",),
            ),
        ),
    )
    plan = plan_report(report, MODEL, as_of=date(2026, 10, 12))
    templates = {s.template_id for s in plan.sections if not s.needs_agent}
    assert "funnel" in templates
    assert len(plan.sections) == 3
    flat = report_as_flat_plan(plan)
    # Namespaced step ids
    assert any(s.step_id.startswith("s1.") for s in flat.steps)
    assert any(s.step_id.startswith("s2.") for s in flat.steps)


def test_coverage_partial_when_one_section_fails() -> None:
    report = ReportSpec(
        windows=(WINDOW,),
        sections=(
            SectionSpec(section_id="s1", objective="ok", shape="lookup", metric_ids=("ad_spend",), presentation_intent="kpis"),
            SectionSpec(section_id="s2", objective="bad", shape="funnel", metric_ids=("sessions", "checkouts"), presentation_intent="funnel"),
        ),
    )
    plan = plan_report(report, MODEL, as_of=date(2026, 10, 12))
    # Pretend only s1 acquired data.
    from seleric_swarm.contracts.pipeline import Ref, ResultRow, ResultSet, StageError

    run = PlanRun(
        result_sets=[
            ResultSet(
                ref=Ref(kind="result_set", id="s1.q1"),
                step_id="s1.q1",
                metric_id="ad_spend",
                unit="INR",
                window=WINDOW,
                breakdowns=(),
                filters=(),
                rows=(ResultRow(evidence_id="e1", dimensions={}, value=100.0),),
            ),
            ResultSet(
                ref=Ref(kind="result_set", id="s2.q1"),
                step_id="s2.q1",
                metric_id="sessions",
                unit="count",
                window=WINDOW,
                breakdowns=(),
                filters=(),
                rows=(),
                error=StageError(stage="execute", code="query_failed", message="down"),
            ),
        ]
    )
    coverage = build_report_coverage(plan, run, stats=AcquireStats(query_calls=2), read_model=MODEL)
    assert coverage.status == "partial"
    by_id = {s.section_id: s.status for s in coverage.sections}
    assert by_id["s1"] in ("complete", "provisional")
    assert by_id["s2"] in ("query_failed", "partial", "unavailable")

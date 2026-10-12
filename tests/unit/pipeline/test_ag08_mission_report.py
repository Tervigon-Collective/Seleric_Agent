"""AG-08 end-to-end: multi-objective report mission with FactPort."""

from __future__ import annotations

from datetime import date, datetime

from seleric_swarm.contracts.pipeline import (
    AnswerDraft,
    Claim,
    MeasurePhrase,
    ObjectiveDraft,
    Paragraph,
    QuestionDraft,
    Ref,
    WindowExpr,
    WindowSlot,
)
from seleric_swarm.pipeline import ShadowInputs, run_mission
from seleric_swarm.semantic import MetricsQueryArgs, QueryResponse, SemanticReadModel

AS_OF = datetime(2026, 10, 12, 12, 0, 0)

MODEL = SemanticReadModel.from_payload(
    {
        "metrics": [
            {"id": "sessions", "label": "Sessions", "aggregation": "additive", "unit": "count", "grain": "session", "view": "web_sessions", "supported_dimensions": []},
            {"id": "checkouts", "label": "Checkouts", "aggregation": "additive", "unit": "count", "grain": "session", "view": "web_sessions", "supported_dimensions": []},
            {"id": "purchases", "label": "Purchases", "aggregation": "additive", "unit": "count", "grain": "session", "view": "web_sessions", "supported_dimensions": []},
            {"id": "ad_spend", "label": "Ad spend", "aggregation": "additive", "unit": "INR", "grain": "ad_day", "view": "paid_media", "category": "paid_media", "supported_dimensions": ["campaign", "campaign_id"]},
            {"id": "net_profit", "label": "Net profit", "aggregation": "additive", "unit": "INR", "grain": "brand_order_day_channel_ad", "view": "order_pnl", "category": "profit", "supported_dimensions": ["campaign", "campaign_id"]},
        ],
        "dimensions": [
            {"id": "campaign", "stable_key": "campaign_id"},
            {"id": "campaign_id"},
        ],
    }
)

FACTS = {
    ("sessions", None): 1000.0,
    ("checkouts", None): 200.0,
    ("purchases", None): 40.0,
    ("ad_spend", None): 50000.0,
    ("net_profit", None): 12000.0,
}


class FactPort:
    async def resolve_concept(self, words: str):
        from seleric_swarm.semantic import ConceptResolution

        key = words.casefold().replace(" ", "_")
        for mid in MODEL.metric_ids():
            if mid in key or key in mid:
                return ConceptResolution(metric_id=mid, suggestions=())
        return ConceptResolution(metric_id=None, suggestions=())

    async def query(self, args: MetricsQueryArgs) -> QueryResponse:
        rows = []
        for measure in args.measures:
            if args.dimensions:
                # Named campaigns plus one null identity.
                rows.append({"campaign_id": "c1", "campaign": "Alpha", measure: 30000.0 if measure == "ad_spend" else 8000.0})
                rows.append({"campaign_id": "c2", "campaign": "Beta", measure: 20000.0 if measure == "ad_spend" else 4000.0})
                rows.append({"campaign_id": None, "campaign": None, measure: 5000.0 if measure == "ad_spend" else -1000.0})
            else:
                value = FACTS.get((measure, None), 0.0)
                rows.append({measure: value})
        return QueryResponse(rows=tuple(rows))


DRAFT = QuestionDraft(
    kind="analysis",
    shape="funnel",
    measures=(
        MeasurePhrase(words="sessions", metric_id="sessions"),
        MeasurePhrase(words="checkouts", metric_id="checkouts"),
        MeasurePhrase(words="purchases", metric_id="purchases"),
        MeasurePhrase(words="ad spend", metric_id="ad_spend"),
        MeasurePhrase(words="net profit", metric_id="net_profit"),
    ),
    windows=(
        WindowSlot(
            role="event",
            expr=WindowExpr(start_iso="2026-10-01", end_iso="2026-10-11"),
            source_span="1-11th October",
        ),
    ),
    names_period=True,
    objectives=(
        ObjectiveDraft(
            objective="Full Funnel Diagnostic Drop Off Analysis",
            shape="funnel",
            measures=(
                MeasurePhrase(words="sessions", metric_id="sessions"),
                MeasurePhrase(words="checkouts", metric_id="checkouts"),
                MeasurePhrase(words="purchases", metric_id="purchases"),
            ),
            presentation_intent="funnel",
        ),
        ObjectiveDraft(
            objective="Meta Ads Top Performer",
            shape="ranking",
            measures=(MeasurePhrase(words="ad spend", metric_id="ad_spend"),),
            entity_dimension="campaign",
            rank_by=MeasurePhrase(words="ad spend", metric_id="ad_spend"),
            presentation_intent="ranking",
        ),
        ObjectiveDraft(
            objective="Net Profit Performer",
            shape="ranking",
            measures=(MeasurePhrase(words="net profit", metric_id="net_profit"),),
            entity_dimension="campaign",
            rank_by=MeasurePhrase(words="net profit", metric_id="net_profit"),
            presentation_intent="ranking",
        ),
        ObjectiveDraft(
            objective="Overall Data",
            shape="lookup",
            measures=(
                MeasurePhrase(words="ad spend", metric_id="ad_spend"),
                MeasurePhrase(words="net profit", metric_id="net_profit"),
                MeasurePhrase(words="sessions", metric_id="sessions"),
            ),
            presentation_intent="kpis",
        ),
    ),
)


def _understand(output=DRAFT):
    async def call(*, instructions: str, prompt: str):
        return output, {}

    return call


async def _answer_llm(*, instructions: str, prompt: str):
    return AnswerDraft(
        status="completed",
        claims=(
            Claim(claim_id="s", ref=Ref(kind="result_set", id="s1.q1"), label="Sessions", role="total"),
        ),
        blocks=(Paragraph(kind="paragraph", text="Sessions were {s} across the funnel window."),),
        next_step="Inspect the largest funnel drop-off by traffic source?",
    ), {}


async def test_multi_objective_report_answers_with_coverage_and_parts() -> None:
    inputs = ShadowInputs(
        question="I NEED Full Funnel Diagnostic (1-11th October). Drop Off Analysis, Top Learnings, Meta Ads, Top Performer and Net Profit Performer, and Overall Data.",
        as_of=date(2026, 10, 12),
        as_of_time=AS_OF,
        read_model=MODEL,
        port=FactPort(),  # type: ignore[arg-type]
    )
    out = await run_mission(_understand(), _answer_llm, inputs, mission_id="ag08")
    assert out.status == "answered", out.trace
    assert out.report_plan is not None
    assert len(out.report_plan.sections) == 4
    templates = {s.template_id for s in out.report_plan.sections}
    assert "funnel" in templates
    assert out.coverage is not None
    assert out.coverage.status in ("complete", "partial", "provisional")
    assert out.run is not None
    assert out.run.query_calls >= 1
    assert out.doc is not None
    kinds = {p.get("type") for p in out.doc.parts}
    assert "KPI_GRID" in kinds or "CHART" in kinds or "TABLE" in kinds
    # Funnel derivation present
    assert any(d.op == "funnel_stages" for d in out.run.derivations)
    # Windows preserved
    assert out.report_spec is not None
    assert out.report_spec.windows
    assert out.report_spec.windows[0].start == date(2026, 10, 1)
    assert out.report_spec.windows[0].end == date(2026, 10, 11)

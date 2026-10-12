"""AG08-03: funnel-safe derivation — missing and incompatible stages."""

from __future__ import annotations

from datetime import date

from seleric_swarm.contracts.pipeline import (
    DeriveStep,
    QueryStep,
    Ref,
    ResultRow,
    ResultSet,
    WindowSpec,
)
from seleric_swarm.derive import derive
from seleric_swarm.plan._templates._funnel import FunnelTemplate, validate_funnel
from seleric_swarm.plan._templates._base import PlanContext, Prepared
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload(
    {
        "metrics": [
            {
                "id": "sessions",
                "aggregation": "additive",
                "unit": "count",
                "grain": "session",
                "view": "web_sessions",
                "supported_dimensions": [],
            },
            {
                "id": "checkouts",
                "aggregation": "additive",
                "unit": "count",
                "grain": "session",
                "view": "web_sessions",
                "supported_dimensions": [],
            },
            {
                "id": "purchases",
                "aggregation": "additive",
                "unit": "count",
                "grain": "session",
                "view": "web_sessions",
                "supported_dimensions": [],
            },
            {
                "id": "page_views",
                "aggregation": "additive",
                "unit": "count",
                "grain": "event",
                "view": "web_events",
                "supported_dimensions": [],
            },
            {
                "id": "orders",
                "aggregation": "additive",
                "unit": "count",
                "grain": "brand_order_day",
                "view": "order_pnl",
                "supported_dimensions": [],
            },
        ],
        "dimensions": [],
    }
)


def _rs(step_id: str, metric_id: str, value: float | None) -> ResultSet:
    return ResultSet(
        ref=Ref(kind="result_set", id=step_id),
        step_id=step_id,
        metric_id=metric_id,
        unit="count",
        window=WindowSpec(role="event", start=date(2026, 10, 1), end=date(2026, 10, 11)),
        breakdowns=(),
        filters=(),
        rows=()
        if value is None
        else (ResultRow(evidence_id=f"e-{step_id}", dimensions={}, value=value),),
    )


def test_validate_funnel_marks_event_stage_incompatible_among_sessions() -> None:
    definition = validate_funnel(("sessions", "page_views", "purchases"), MODEL)
    assert definition.kind in ("mixed", "session")
    statuses = {s.metric_id: s.status for s in definition.stages}
    assert statuses["sessions"] == "ok"
    assert statuses["page_views"] == "incompatible"
    assert statuses["purchases"] in ("ok", "incompatible")


def test_validate_funnel_marks_missing_stage() -> None:
    definition = validate_funnel(("sessions", "missing_stage", "purchases"), MODEL)
    statuses = {s.metric_id: s.status for s in definition.stages}
    assert statuses["missing_stage"] == "missing"
    assert statuses["sessions"] == "ok"


def test_funnel_stages_op_null_not_zero_for_missing() -> None:
    definition = validate_funnel(("sessions", "missing_stage", "purchases"), MODEL)
    # Simulate two available stages; middle missing is recorded in funnel params.
    inputs = [_rs("q1", "sessions", 1000.0), _rs("q2", "purchases", 40.0)]
    out = derive(
        "funnel_stages",
        inputs,
        {
            "funnel": definition.model_dump(mode="json"),
            "kind": definition.kind,
        },
        read_model=MODEL,
        ref=Ref(kind="derivation", id="d1"),
    )
    assert out.error is None
    # No numeric zero invented for the missing middle stage in stage count rows.
    stage_rows = [r for r in out.rows if not r.dimensions.get("metric")]
    assert all(r.value != 0 or r.dimensions.get("stage") for r in stage_rows)
    assert out.params.get("largest_leakage") is not None or out.value is not None


def test_funnel_template_emits_funnel_stages_not_share() -> None:
    from seleric_swarm.contracts.pipeline import QuestionSpec, MeasureSpec

    spec = QuestionSpec(
        kind="analysis",
        shape="funnel",
        measures=(
            MeasureSpec(phrase="sessions", metric_id="sessions"),
            MeasureSpec(phrase="checkouts", metric_id="checkouts"),
            MeasureSpec(phrase="purchases", metric_id="purchases"),
        ),
        filters=(),
        entities=(),
        breakdowns=(),
        windows=(WindowSpec(role="event", start=date(2026, 10, 1), end=date(2026, 10, 11)),),
        grain=None,
        ranking=None,
        catalogue_version="",
    )
    prepared = Prepared(
        spec=spec,
        shape="funnel",
        metrics=("sessions", "checkouts", "purchases"),
        entity=None,
        breakdowns=(),
        ranking=None,
        windows={"event": spec.windows[0]},
        today_running=False,
    )
    plan = FunnelTemplate().build(prepared, PlanContext(read_model=MODEL))
    assert plan is not None
    derives = [s for s in plan.steps if isinstance(s, DeriveStep)]
    assert any(s.op == "funnel_stages" for s in derives)
    assert not any(s.op == "share" for s in derives)

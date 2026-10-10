"""run_plan end to end on a fact-table fake port: plan → execute → derive → investigate → answer."""

from __future__ import annotations

from datetime import UTC, date, datetime

from seleric_swarm.answer import Evidence, answer, verify
from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    AnswerDraft,
    Claim,
    DeriveStep,
    EngineStep,
    MeasureSpec,
    Paragraph,
    QueryStep,
    QuestionSpec,
    Ref,
    WindowSpec,
)
from seleric_swarm.pipeline import run_plan
from seleric_swarm.plan import plan as build_plan
from seleric_swarm.semantic import MetricsQueryArgs, QueryResponse, SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [{"id": "zz_take", "aggregation": "additive", "unit": "INR", "supported_dimensions": ["zz_zone", "zz_device"]}],
    "dimensions": [{"id": "zz_zone"}, {"id": "zz_device"}],
})
EVENT = WindowSpec(role="event", start=date(2026, 10, 9), end=date(2026, 10, 9))
BASE = WindowSpec(role="baseline", start=date(2026, 10, 8), end=date(2026, 10, 8))
AS_OF = datetime(2026, 10, 10, 12, tzinfo=UTC)
FACTS = [
    {"day": d, "zz_zone": z, "zz_device": v, "zz_take": t}
    for d, cut in (("2026-10-08", 100.0), ("2026-10-09", 10.0))
    for z, v, t in (("north", "mob", cut), ("north", "desk", 100.0), ("south", "mob", 100.0), ("south", "desk", 100.0))
]


class FactPort:
    def __init__(self) -> None:
        self.calls = 0

    async def query(self, args: MetricsQueryArgs) -> QueryResponse:
        self.calls += 1
        metric = args.measures[0]
        rows = [r for r in FACTS if args.time_range["start"] <= r["day"] <= args.time_range["end"]]
        for f in args.filters:
            keep = f["operator"] == "equals"
            rows = [r for r in rows if (r[f["dimension"]] in f["values"]) == keep]
        out: dict[tuple[str, ...], float] = {}
        for r in rows:
            key = tuple(r[d] for d in args.dimensions)
            out[key] = out.get(key, 0.0) + r[metric]
        return QueryResponse(rows=tuple({**dict(zip(args.dimensions, k, strict=True)), metric: v} for k, v in out.items()))


def spec(shape: str) -> QuestionSpec:
    return QuestionSpec(kind="analysis", shape=shape, measures=(MeasureSpec(phrase="take", metric_id="zz_take"),),
                        filters=(), entities=(), breakdowns=(), windows=(EVENT, BASE), grain=None, ranking=None,
                        catalogue_version="zz-v1", direction="down")


async def test_period_comparison_queries_and_derives() -> None:
    s = spec("period_comparison")
    p = build_plan(s, MODEL, as_of=date(2026, 10, 10))
    run = await run_plan(p, s, port=FactPort(), read_model=MODEL, as_of=AS_OF)
    assert run.summary()["step_errors"] == [] and run.summary()["derive_errors"] == []
    by_op = {d.op: d.value for d in run.derivations}
    assert by_op["delta"] == -90.0 and abs(by_op["pct_change"] - (-0.225)) < 1e-9


async def test_why_runs_the_investigation_engine() -> None:
    s = spec("why_single_metric")
    p = build_plan(s, MODEL, as_of=date(2026, 10, 10))
    assert p.template_id == "why"
    run = await run_plan(p, s, port=FactPort(), read_model=MODEL, as_of=AS_OF)
    (state,) = run.investigations.values()
    assert state.status == "done"
    segs = [d.params["subject"] for d in run.derivations if d.op == "finding:segment"]
    assert segs == ["zz_zone=north", "zz_device=mob"]


async def test_missing_input_and_unsupported_engine_are_recorded() -> None:
    p = AnalysisPlan(template_id="x", steps=(
        QueryStep(step_id="q1", metric_id="zz_take", breakdowns=(), filters=(), window_role="event", grain=None),
        DeriveStep(step_id="d1", op="delta", inputs=("q0", "q1")),
        EngineStep(step_id="e1", engine="explore", params={}),
    ))
    run = await run_plan(p, spec("lookup"), port=FactPort(), read_model=MODEL, as_of=AS_OF)
    assert run.summary()["derive_errors"] == ["missing_input"] and run.unsupported == ["explore"]


async def test_answer_over_run_evidence_verifies() -> None:
    s = spec("period_comparison")
    run = await run_plan(build_plan(s, MODEL, as_of=date(2026, 10, 10)), s, port=FactPort(), read_model=MODEL, as_of=AS_OF)
    ev = Evidence(run.evidence)
    q_event = next(r for r in run.result_sets if r.window.role == "event")
    q_base = next(r for r in run.result_sets if r.window.role == "baseline")
    pct = next(d for d in run.derivations if d.op == "pct_change")
    draft = AnswerDraft(status="completed", claims=(
        Claim(claim_id="now", ref=q_event.ref, label="Take", role="total"),
        Claim(claim_id="was", ref=q_base.ref, label="Take", role="context"),
        Claim(claim_id="chg", ref=Ref(kind="derivation", id=pct.ref.id), label="Take", role="change"),
    ), blocks=(Paragraph(kind="paragraph", text="Take fell to {now} from {was} ({chg})."),))
    assert verify(draft, s, ev).ok

    async def llm(*, instructions: str, prompt: str):
        return draft, {}

    doc, _ = await answer(llm, "why did take fall", spec=s, evidence=ev, read_model=MODEL)
    assert doc.rendered_markdown == "Take fell to INR 310 from INR 400 (-22.5%)."

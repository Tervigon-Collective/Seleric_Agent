"""explore (M4.4) and composition engines through run_plan on a daily fact-table port."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    EngineStep,
    MeasureSpec,
    QuestionSpec,
    WindowSpec,
)
from seleric_swarm.pipeline import continue_exploration, run_plan
from seleric_swarm.semantic import MetricsQueryArgs, QueryResponse, SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [
        {"id": "zz_take", "aggregation": "additive", "supported_dimensions": ["zz_zone"]},
        {"id": "zz_refunds", "aggregation": "additive"},
        {"id": "zz_net", "aggregation": "additive",
         "composition": {"terms": [{"metric": "zz_take"}, {"metric": "zz_refunds", "coefficient": -1}]}},
    ],
    "dimensions": [{"id": "zz_zone"}],
})
AS_OF = datetime(2026, 10, 10, 12, tzinfo=UTC)
LAST = date(2026, 10, 9)
EVENT = WindowSpec(role="event", start=LAST - timedelta(days=6), end=LAST)
BASE = WindowSpec(role="baseline", start=LAST - timedelta(days=13), end=LAST - timedelta(days=7))


def _facts():
    rows = []
    for i in range(60):
        day = LAST - timedelta(days=i)
        north = 20.0 if i < 7 else 100.0 + (i % 3)
        for zone, take in (("north", north), ("south", 100.0 + (i % 2)), ("east", 50.0 + (i % 4))):
            rows.append({"day": day.isoformat(), "zz_zone": zone, "zz_take": take, "zz_refunds": 5.0, "zz_net": take - 5.0})
    return rows


FACTS = _facts()


class DailyPort:
    async def query(self, args: MetricsQueryArgs) -> QueryResponse:
        metric = args.measures[0]
        rows = [r for r in FACTS if args.time_range["start"] <= r["day"] <= args.time_range["end"]]
        for f in args.filters:
            rows = [r for r in rows if (r[f["dimension"]] in f["values"]) == (f["operator"] == "equals")]
        keys = list(args.dimensions) + (["day"] if args.granularity == "day" else [])
        out: dict[tuple[str, ...], float] = {}
        for r in rows:
            k = tuple(r[d] for d in keys)
            out[k] = out.get(k, 0.0) + r[metric]
        res = []
        for k, v in out.items():
            row = {d: x for d, x in zip(keys, k, strict=True) if d != "day"}
            if "day" in keys:
                row["v.report_date.day"] = k[-1] + "T00:00:00"
            res.append({**row, metric: v})
        return QueryResponse(rows=tuple(res))


def _spec(shape: str, kind: str = "analysis", windows=(EVENT,)) -> QuestionSpec:
    return QuestionSpec(kind=kind, shape=shape, measures=(MeasureSpec(phrase="take", metric_id="zz_take"),), filters=(),
                        entities=(), breakdowns=(), windows=windows, grain=None, ranking=None, catalogue_version="zz-v1")


async def test_explore_finds_the_collapse_and_continues_without_repeats() -> None:
    plan = AnalysisPlan(template_id="exploration", steps=(EngineStep(step_id="e1", engine="explore", params={"metric_ids": ["zz_take"]}),))
    spec = _spec("lookup", "exploration")
    run = await run_plan(plan, spec, port=DailyPort(), read_model=MODEL, as_of=AS_OF)
    xmap = run.explorations["e1"]
    assert xmap.insights and xmap.window == (EVENT.start, LAST)
    assert any("north" in (i.get("segments") or []) for i in xmap.insights)
    assert all(s.engine in {"explore", "investigate", "query"} for s in xmap.follow_ups)
    again = await run_plan(AnalysisPlan(template_id="exploration", steps=(continue_exploration(xmap),)), spec,
                           port=DailyPort(), read_model=MODEL, as_of=AS_OF)
    first = {(i["kind"], i["metric"], i.get("dimension")) for i in xmap.insights}
    second = {(i["kind"], i["metric"], i.get("dimension")) for i in again.explorations["e1"].insights}
    assert not first & second


async def test_composition_reads_components_and_their_change() -> None:
    plan = AnalysisPlan(template_id="composition", steps=(EngineStep(step_id="c", engine="composition", params={"metric_id": "zz_net"}),))
    run = await run_plan(plan, _spec("composition", windows=(EVENT, BASE)), port=DailyPort(), read_model=MODEL, as_of=AS_OF)
    deltas = {d.ref.id: d.value for d in run.derivations}
    assert deltas["c.zz_refunds.delta"] == 0.0
    assert deltas["c.zz_net.delta"] == deltas["c.zz_take.delta"] < 0

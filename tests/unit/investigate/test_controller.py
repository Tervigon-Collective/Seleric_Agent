"""M4.3 controller on planted incidents (invented catalogue, synthetic fact table)."""

from __future__ import annotations

from datetime import date

from seleric_swarm.context import CalendarProvider, ContextWindow, DataHealth
from seleric_swarm.contracts.pipeline import FilterSpec, WindowSpec
from seleric_swarm.forecasting.calendar import CalendarConfig, FestivalEntry
from seleric_swarm.graph import build_graph
from seleric_swarm.investigate import (
    InvestigatePolicy,
    InvestigateRequest,
    Investigation,
    InvestigationState,
)
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [
        {"id": "zz_take", "aggregation": "additive", "supported_dimensions": ["zz_zone", "zz_device"]},
        {"id": "zz_count", "aggregation": "additive", "supported_dimensions": ["zz_zone", "zz_device"]},
        {"id": "zz_visits", "aggregation": "additive"},
        {"id": "zz_avg", "aggregation": "ratio", "composition": {"numerator": "zz_take", "denominator": "zz_count"}},
    ],
    "dimensions": [{"id": "zz_zone"}, {"id": "zz_device"}],
})
GRAPH = build_graph(MODEL, {"graphs": [{"id": "g", "edges": [["zz_visits", "zz_take", 1, 0]]}]})
BASE = WindowSpec(role="baseline", start=date(2026, 10, 1), end=date(2026, 10, 1))
EVENT = WindowSpec(role="event", start=date(2026, 10, 2), end=date(2026, 10, 2))
CELLS = [("north", "mob"), ("north", "desk"), ("south", "mob"), ("south", "desk")]


def facts(event_take: dict[tuple[str, str], float], visits=(100.0, 100.0)):
    rows = []
    for zone, dev in CELLS:
        for role, take in (("baseline", 100.0), ("event", event_take.get((zone, dev), 100.0))):
            rows.append({"role": role, "zz_zone": zone, "zz_device": dev, "zz_take": take, "zz_count": 50.0})
    return rows, {"baseline": visits[0], "event": visits[1]}


class Reader:
    def __init__(self, rows, visits) -> None:
        self.rows, self.visits = rows, visits

    def _match(self, window, filters):
        return [r for r in self.rows if r["role"] == window.role
                and all(r.get(f.dimension) in f.values for f in filters)]

    async def total(self, metric_id, window, filters):
        if metric_id == "zz_visits":
            return self.visits[window.role]
        if metric_id == "zz_avg":
            rows = self._match(window, filters)
            return sum(r["zz_take"] for r in rows) / sum(r["zz_count"] for r in rows)
        return sum(r[metric_id] for r in self._match(window, filters))

    async def breakdown(self, metric_id, window, dimension, filters):
        out: dict[str, float] = {}
        for r in self._match(window, filters):
            out[r[dimension]] = out.get(r[dimension], 0.0) + r[metric_id]
        return out


def req(metric="zz_take", direction="either") -> InvestigateRequest:
    return InvestigateRequest(metric_id=metric, event=EVENT, baseline=BASE, direction=direction)


async def test_segment_collapse_found_at_depth_two() -> None:
    st = await Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH).run(req())
    segs = [f for f in st.findings if f.kind == "segment"]
    assert [(f.depth, f.subject) for f in segs] == [(0, "zz_zone=north"), (1, "zz_device=mob")]
    assert st.status == "done" and st.explained_share == 1.0
    assert st.path == (FilterSpec(dimension="zz_zone", operator="in", values=("north",)),)


async def test_premise_false_stops() -> None:
    st = await Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH).run(req(direction="up"))
    assert st.status == "premise_false" and [f.kind for f in st.findings] == ["premise"]
    flat = await Investigation(Reader(*facts({})), MODEL, GRAPH).run(req())
    assert flat.status == "premise_false" and flat.findings[0].verdict == "not_material"


async def test_festival_is_cited() -> None:
    cal = CalendarProvider(CalendarConfig(festivals=[FestivalEntry(id="zz_fest", dates=["2026-10-02"])], pre_festival_days=0,
                                          payday={"month_start_days": [], "month_end_days": []}, month_end_days=[]))
    st = await Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH, providers=[cal]).run(req())
    assert [(f.subject, f.verdict) for f in st.findings if f.kind == "context"] == [("zz_fest", "festival")]


async def test_incomplete_data_stops_the_run() -> None:
    class Bad:
        id = "bad"

        def health(self, window: ContextWindow, metric_ids, read_model):
            return [DataHealth(metric_id="zz_take", status="incomplete")]

    st = await Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH, health_sources=[Bad()]).run(req())
    assert st.status == "unhealthy" and "data_incomplete" in st.limitations


async def test_ratio_decomposes_to_numerator_and_skips_localise() -> None:
    st = await Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH).run(req("zz_avg"))
    comps = {f.subject: f.share_of_change for f in st.findings if f.kind == "component"}
    assert abs(comps["zz_take"] - 1.0) < 1e-9 and abs(comps["zz_count"]) < 1e-9
    assert "localise_skipped_non_additive" in st.limitations


async def test_driver_supported_only_when_it_moved_the_expected_way() -> None:
    down = await Investigation(Reader(*facts({("north", "mob"): 10.0}, visits=(100.0, 70.0))), MODEL, GRAPH).run(req())
    assert [f.verdict for f in down.findings if f.kind == "driver"] == ["supported_cause"]
    up = await Investigation(Reader(*facts({("north", "mob"): 10.0}, visits=(100.0, 130.0))), MODEL, GRAPH).run(req())
    assert [f.verdict for f in up.findings if f.kind == "driver"] == ["inconsistent"]


async def test_budget_and_selector() -> None:
    st = await Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH,
                             policy=InvestigatePolicy(max_reads=3)).run(req())
    assert st.status == "budget" and "read_budget_spent" in st.limitations

    async def pick_device(*, stage, candidates):
        return next(c.id for c in candidates if c.id.startswith("zz_device")), "device first"

    st2 = await Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH, selector=pick_device).run(req())
    assert next(f.subject for f in st2.findings if f.kind == "segment") == "zz_device=mob"


async def test_resume_digs_from_stored_path() -> None:
    inv = Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH, policy=InvestigatePolicy(max_depth=1))
    st = await inv.run(req())
    assert [f.depth for f in st.findings if f.kind == "segment"] == [0]
    deeper = Investigation(Reader(*facts({("north", "mob"): 10.0})), MODEL, GRAPH)
    st = await deeper.run(req(), InvestigationState.model_validate(st.model_dump()))
    assert st.status == "done"
    assert [(f.depth, f.subject) for f in st.findings if f.kind == "segment"] == [(0, "zz_zone=north"), (1, "zz_device=mob")]

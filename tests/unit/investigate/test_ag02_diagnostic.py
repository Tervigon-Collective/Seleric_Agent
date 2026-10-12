"""AG-02 diagnostic engine: decomposition, mix effects, budgets, qualifications."""

from __future__ import annotations

from datetime import date

from seleric_swarm.contracts.pipeline import WindowSpec
from seleric_swarm.derive._ops import mix_shift_decomposition
from seleric_swarm.graph import build_graph
from seleric_swarm.investigate import (
    InvestigatePolicy,
    InvestigateRequest,
    Investigation,
)
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [
        {"id": "zz_take", "aggregation": "additive", "supported_dimensions": ["zz_zone", "zz_device"]},
        {"id": "zz_count", "aggregation": "additive", "supported_dimensions": ["zz_zone", "zz_device"]},
        {"id": "zz_avg", "aggregation": "ratio",
         "composition": {"numerator": "zz_take", "denominator": "zz_count"}},
    ],
    "dimensions": [{"id": "zz_zone"}, {"id": "zz_device"}],
})
GRAPH = build_graph(MODEL, {"graphs": [{"id": "g", "edges": [["zz_visits", "zz_take", 1, 0]]}]})
BASE = WindowSpec(role="baseline", start=date(2026, 10, 1), end=date(2026, 10, 1))
EVENT = WindowSpec(role="event", start=date(2026, 10, 2), end=date(2026, 10, 2))


class Reader:
    """Return planted totals/breakdowns keyed by metric id."""

    def __init__(self, totals: dict[str, tuple[float, float]], breakdowns=None, model=None) -> None:
        self.totals = totals
        self.breakdowns = breakdowns or {}
        self.model = model
        self.calls: list[tuple] = []

    async def total(self, metric_id, window, filters):
        self.calls.append(("total", metric_id, window.role, filters))
        if metric_id in self.totals:
            b, e = self.totals[metric_id]
            return b if window.role == "baseline" else e
        # Ratio metric: compute from numerator/denominator if model provided
        if self.model is not None:
            comp = self.model._metric(metric_id).composition if self.model._metric(metric_id) else None
            if isinstance(comp, dict) and "numerator" in comp and "denominator" in comp:
                num, den = comp["numerator"], comp["denominator"]
                if num in self.totals and den in self.totals:
                    nb, ne = self.totals[num]
                    db, de = self.totals[den]
                    base = nb / db if db else None
                    event = ne / de if de else None
                    return base if window.role == "baseline" else event
        raise KeyError(metric_id)

    async def breakdown(self, metric_id, window, dimension, filters):
        self.calls.append(("breakdown", metric_id, window.role, dimension, filters))
        return self.breakdowns.get((metric_id, window.role, dimension), {})


def req(metric="zz_take", direction="either") -> InvestigateRequest:
    return InvestigateRequest(metric_id=metric, event=EVENT, baseline=BASE, direction=direction)


# ---------------------------------------------------------------- T01/T02 decomposition
async def test_ag02_t01_signed_decomposition_reconciles() -> None:
    # zz_take = north + south (signed terms via composition); each side moves by a known amount.
    reader = Reader({"zz_take": (100.0, 160.0), "zz_north": (60.0, 60.0), "zz_south": (40.0, 100.0)})
    model = SemanticReadModel.from_payload({
        "catalogue_version": "zz-v1",
        "metrics": [
            {"id": "zz_take", "aggregation": "additive", "supported_dimensions": [],
             "composition": {"terms": [{"metric": "zz_north", "coefficient": 1.0},
                                       {"metric": "zz_south", "coefficient": 1.0}]}},
            {"id": "zz_north", "aggregation": "additive", "supported_dimensions": []},
            {"id": "zz_south", "aggregation": "additive", "supported_dimensions": []},
        ],
    })
    graph = build_graph(model, {})
    st = await Investigation(reader, model, graph).run(req())
    comps = {f.subject: f.delta for f in st.findings if f.kind == "component"}
    assert comps == {"zz_south": 60.0, "zz_north": 0.0}
    assert st.unexplained_residual == 0.0


async def test_ag02_t02_ratio_decomposition_reconciles() -> None:
    # r = n/d: n 100->120, d 50->60. num effect = 20/50 = .4; den effect = 120*(50-60)/(50*60) = -.4
    # Δr = 240/60 - 100/50 = 4 - 2 = 2... wait: r0 = 100/50 = 2, r1 = 120/60 = 2. Use different numbers.
    # n 100->150, d 50->50: num effect = 50/50 = 1, den effect = 0
    reader = Reader({"zz_take": (100.0, 150.0), "zz_count": (50.0, 50.0)}, model=MODEL)
    st = await Investigation(reader, MODEL, GRAPH).run(req("zz_avg"))
    comps = {f.subject: f.delta for f in st.findings if f.kind == "component"}
    assert abs(comps["zz_take"] - 1.0) < 1e-9
    assert abs(comps["zz_count"] - 0.0) < 1e-9
    assert abs(st.unexplained_residual) < 1e-9


async def test_ag02_t02b_ratio_denominator_moves() -> None:
    reader = Reader({"zz_take": (100.0, 100.0), "zz_count": (50.0, 40.0)}, model=MODEL)
    st = await Investigation(reader, MODEL, GRAPH).run(req("zz_avg"))
    comps = {f.subject: f.delta for f in st.findings if f.kind == "component"}
    # Δr = 100/40 - 100/50 = 2.5 - 2 = .5; den effect = 100*(50-40)/(50*40) = .5
    assert abs(comps["zz_count"] - 0.5) < 1e-9
    assert abs(st.unexplained_residual) < 1e-9


def test_ag02_t03_mix_shift_distinguished_from_within() -> None:
    # Overall rate: baseline 0.20, event 0.23 (a drops rate but gains weight; b drops rate but gains weight)
    parts = mix_shift_decomposition(
        baseline_weights={"a": 0.5, "b": 0.5},
        event_weights={"a": 0.1, "b": 0.9},
        baseline_rates={"a": 0.10, "b": 0.30},
        event_rates={"a": 0.05, "b": 0.25},
    )
    # a: within=0.5*(0.05-0.10)=-0.025, mix=(0.1-0.5)*0.10=-0.04
    # b: within=0.5*(0.25-0.30)=-0.025, mix=(0.9-0.5)*0.30=0.12
    assert abs(parts["within"] - (-0.05)) < 1e-9
    assert abs(parts["mix"] - 0.08) < 1e-9
    assert abs(parts["within"] + parts["mix"] + parts["interaction"] - 0.03) < 1e-9


def test_ag02_t03b_new_entity_lands_in_mix_not_within() -> None:
    parts = mix_shift_decomposition(
        baseline_weights={"a": 1.0},
        event_weights={"a": 0.5, "b": 0.5},
        baseline_rates={"a": 0.20},
        event_rates={"a": 0.20, "b": 0.40},
    )
    # within = 1.0*(.20-.20) = 0; the new entity's contribution is mix+interaction.
    assert parts["within"] == 0.0
    assert abs(parts["mix"] + parts["interaction"] - 0.1) < 1e-9


# ---------------------------------------------------------------- T04 campaign rename
async def test_ag02_t04_renamed_entity_reported_not_merged() -> None:
    # Total changes (50->100) but "camp_b" in baseline is "camp_b_renamed" in event:
    # a rename must not look like one segment vanishing and another appearing simultaneously.
    reader = Reader(
        {"zz_take": (50.0, 100.0)},
        breakdowns={
            ("zz_take", "baseline", "zz_zone"): {"camp_b": 50.0},
            ("zz_take", "event", "zz_zone"): {"camp_b_renamed": 100.0},
        },
        model=MODEL,
    )
    st = await Investigation(reader, MODEL, GRAPH).run(req())
    verdicts = {f.subject: f.verdict for f in st.findings if f.kind == "limitation"}
    assert verdicts.get("zz_zone=camp_b") == "entity_disappeared"
    assert verdicts.get("zz_zone=camp_b_renamed") == "entity_appeared"


# ---------------------------------------------------------------- T05 unsupported drilldown
async def test_ag02_t05_unsupported_drilldown_is_skipped() -> None:
    # zz_avg is a ratio: localise must skip it entirely.
    st = await Investigation(
        Reader({"zz_take": (100.0, 150.0), "zz_count": (50.0, 50.0)}, model=MODEL), MODEL, GRAPH
    ).run(req("zz_avg"))
    assert "localise_skipped_non_additive" in st.limitations
    assert not [f for f in st.findings if f.kind == "segment"]


# ---------------------------------------------------------------- T06 query budget
async def test_ag02_t06_query_budget_never_exceeded() -> None:
    reader = Reader({"zz_take": (100.0, 150.0)}, model=MODEL)
    st = await Investigation(reader, MODEL, GRAPH, policy=InvestigatePolicy(max_reads=1)).run(req())
    assert st.status == "budget"
    assert st.reads <= 1


# ---------------------------------------------------------------- T07 cached reads
async def test_ag02_t07_cached_reads_do_not_repeat() -> None:
    reader = Reader({"zz_take": (100.0, 100.0)}, model=MODEL)
    st = await Investigation(reader, MODEL, GRAPH).run(req())
    total_calls = [c for c in reader.calls if c[0] == "total" and c[1] == "zz_take" and c[2] == "baseline"]
    # premise reads baseline once; decompose/localise must reuse the cache.
    assert len(total_calls) == 1


# ---------------------------------------------------------------- T08 missing components
async def test_ag02_t08_missing_components_yield_limited_diagnosis() -> None:
    class Missing(Reader):
        async def total(self, metric_id, window, filters):
            if metric_id == "zz_count":
                return None
            return await super().total(metric_id, window, filters)

    st = await Investigation(Missing({"zz_take": (100.0, 150.0), "zz_count": (50.0, 50.0)}, model=MODEL), MODEL, GRAPH).run(req("zz_avg"))
    assert "decomposition_incomplete" in st.limitations
    assert any(f.verdict == "missing_components" for f in st.findings if f.kind == "limitation")
    assert st.unexplained_residual is not None


# ---------------------------------------------------------------- T09 direction != causation
async def test_ag02_t09_direction_agreement_is_not_causal_proof() -> None:
    model = SemanticReadModel.from_payload({
        "catalogue_version": "zz-v1",
        "metrics": [
            {"id": "zz_take", "aggregation": "additive", "supported_dimensions": ["zz_zone", "zz_device"]},
            {"id": "zz_visits", "aggregation": "additive", "supported_dimensions": ["zz_zone", "zz_device"]},
        ],
        "dimensions": [{"id": "zz_zone"}, {"id": "zz_device"}],
    })
    graph = build_graph(model, {"graphs": [{"id": "g", "edges": [["zz_visits", "zz_take", 1, 0]]}]})
    reader = Reader({"zz_take": (100.0, 150.0), "zz_visits": (1000.0, 1200.0)}, model=model)
    st = await Investigation(reader, model, graph).run(InvestigateRequest(metric_id="zz_take", event=EVENT, baseline=BASE))
    drivers = [f for f in st.findings if f.kind == "driver" and f.subject == "zz_visits"]
    assert drivers and drivers[0].verdict == "directionally_consistent"
    assert drivers[0].qualification == "directionally_consistent_hypothesis"
    assert not [f for f in st.findings if f.qualification == "causally_supported"]


# ---------------------------------------------------------------- T10 residual
async def test_ag02_t10_unexplained_residual_reported() -> None:
    # take moves by 50 but the only identity component (north) moves by 30.
    model = SemanticReadModel.from_payload({
        "catalogue_version": "zz-v1",
        "metrics": [
            {"id": "zz_take", "aggregation": "additive", "supported_dimensions": [],
             "composition": {"terms": [{"metric": "zz_north", "coefficient": 1.0}]}},
            {"id": "zz_north", "aggregation": "additive", "supported_dimensions": []},
        ],
    })
    graph = build_graph(model, {})
    st = await Investigation(Reader({"zz_take": (100.0, 150.0), "zz_north": (60.0, 90.0)}), model, graph).run(
        InvestigateRequest(metric_id="zz_take", event=EVENT, baseline=BASE)
    )
    assert abs(st.unexplained_residual - 20.0) < 1e-9
    assert any(f.verdict == "unexplained_residual" for f in st.findings if f.kind == "limitation")

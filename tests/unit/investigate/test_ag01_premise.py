"""AG-01 premise states and health vocabulary (ADR-007).

A run must never say the premise is false when there was no data, and a
preliminary or unverifiable view must not be reported as a solid number.
"""

from __future__ import annotations

from datetime import date

from seleric_swarm.context import ContextWindow, DataHealth
from seleric_swarm.contracts.pipeline import WindowSpec
from seleric_swarm.graph import build_graph
from seleric_swarm.investigate import (
    InvestigatePolicy,
    InvestigateRequest,
    Investigation,
    aggregation_is_additive,
)
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [
        {"id": "zz_take", "aggregation": "additive", "supported_dimensions": ["zz_zone"]},
        {"id": "zz_avg", "aggregation": "ratio", "composition": {"numerator": "zz_take", "denominator": "zz_take"}},
    ],
    "dimensions": [{"id": "zz_zone"}],
})
GRAPH = build_graph(MODEL, {"graphs": []})
BASE = WindowSpec(role="baseline", start=date(2026, 10, 1), end=date(2026, 10, 1))
EVENT = WindowSpec(role="event", start=date(2026, 10, 2), end=date(2026, 10, 2))


class Reader:
    """Plant the event total directly; the baseline is a fixed base."""

    def __init__(self, event_total: float, baseline_total: float = 100.0) -> None:
        self.event_total, self.baseline_total = event_total, baseline_total

    async def total(self, metric_id, window, filters):
        return self.event_total if window.role == "event" else self.baseline_total

    async def breakdown(self, metric_id, window, dimension, filters):
        return {}


def req(metric="zz_take", direction="either") -> InvestigateRequest:
    return InvestigateRequest(metric_id=metric, event=EVENT, baseline=BASE, direction=direction)


class Health:
    id = "health"

    def __init__(self, *statuses: str, metric_id: str = "zz_take") -> None:
        self.statuses = statuses
        self.metric_id = metric_id

    def health(self, window: ContextWindow, metric_ids, read_model):
        return [DataHealth(metric_id=self.metric_id, status=s) for s in self.statuses]


async def run(reader: Reader, direction="either", metric="zz_take", health=()):
    return await Investigation(reader, MODEL, GRAPH, health_sources=list(health)).run(req(metric, direction))


async def test_ag01_t01_opposite_is_the_only_premise_false() -> None:
    st = await run(Reader(10.0), direction="up")
    assert st.status == "premise_false" and st.findings[0].verdict == "opposite"
    assert st.stage == "stop"


async def test_ag01_t02_no_material_change_is_not_material() -> None:
    st = await run(Reader(100.5), direction="up")
    assert st.status == "not_material" and st.findings[0].verdict == "not_material"


async def test_ag01_t03_unreadable_window_is_insufficient_evidence() -> None:
    class Missing(Reader):
        async def total(self, metric_id, window, filters):
            return None if window.role == "event" else self.baseline_total

    st = await Investigation(Missing(10.0), MODEL, GRAPH).run(req())
    assert st.status == "insufficient_evidence"
    assert st.findings[0].verdict == "insufficient_evidence"
    assert st.event_total is None


async def test_ag01_t04_confirmed_premise_proceeds() -> None:
    st = await run(Reader(200.0), direction="up")
    assert st.status == "done" and st.findings[0].verdict == "confirmed"


async def test_ag01_t05_provisional_is_recorded_not_fatal() -> None:
    st = await run(Reader(200.0), direction="up", health=[Health("provisional")])
    assert st.status == "done" and "data_provisional" in st.limitations


async def test_ag01_t06_unknown_view_stops_the_run() -> None:
    st = await run(Reader(200.0), direction="up", health=[Health("unknown")])
    assert st.status == "unhealthy" and "data_unknown" in st.limitations


async def test_ag01_t06b_unknown_on_component_does_not_stop() -> None:
    st = await run(Reader(200.0), direction="up", health=[Health("unknown", metric_id="zz_avg")])
    assert st.status == "done" and "data_health_issues" in st.limitations


async def test_ag01_t07_incomplete_stops_with_distinct_reason() -> None:
    st = await run(Reader(200.0), direction="up", health=[Health("incomplete")])
    assert st.status == "unhealthy" and "data_incomplete" in st.limitations


async def test_ag01_t08_freshness_maps_raw_statuses() -> None:
    from seleric_swarm.context import FreshnessHealth

    model = SemanticReadModel.from_payload({
        "catalogue_version": "zz-v1",
        "metrics": [{"id": "zz_take", "aggregation": "additive", "view": "v_take"}],
        "views": [{"id": "v_take", "freshness": "preliminary"}],
    })
    got = FreshnessHealth().health(
        ContextWindow(start=date(2026, 10, 1), end=date(2026, 10, 2)), ("zz_take",), model
    )
    assert [(h.metric_id, h.status) for h in got] == [("zz_take", "provisional")]


async def test_ag01_t09_unknown_status_is_visible_not_assumed() -> None:
    from seleric_swarm.context import FreshnessHealth

    model = SemanticReadModel.from_payload({
        "catalogue_version": "zz-v1",
        "metrics": [{"id": "zz_take", "aggregation": "additive", "view": "v_take"}],
        "views": [{"id": "v_take", "freshness": "weird"}],
    })
    got = FreshnessHealth().health(
        ContextWindow(start=date(2026, 10, 1), end=date(2026, 10, 2)), ("zz_take",), model
    )
    assert [h.status for h in got] == ["unknown"]


def test_ag01_t10_aggregation_is_additive_is_shared() -> None:
    assert aggregation_is_additive("additive") is True
    assert aggregation_is_additive(None) is True
    assert aggregation_is_additive("ratio") is False
    assert aggregation_is_additive("count_distinct") is False

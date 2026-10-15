"""Port contract suites (plan P1.14): what every adapter of a port must do, run against each adapter we have.

SeriesPort: file adapter and Cube adapter. GraphPort: the vendored-file adapter. EvidencePort: an in-memory stand-in
(no production adapter exists yet). RunStore joins when P1.7 lands."""

from __future__ import annotations

from datetime import date

import pytest

from seleric_swarm.diagnosis.adapters.catalogue_graph import VendoredGraphAdapter
from seleric_swarm.diagnosis.domain.graph import LoadedGraph
from seleric_swarm.diagnosis.domain.types import Scope, SeriesRequest, Window, result_fingerprint
from seleric_swarm.diagnosis.ports.evidence import DatedEvidence

WINDOW = Window(date(2026, 9, 10), date(2026, 9, 20))


@pytest.fixture(params=["file", "cube"])
def series_adapter(request):
    return request.getfixturevalue("file_adapter" if request.param == "file" else "cube_adapter")


async def test_series_port_answers_every_request_in_order(series_adapter):
    requests = [
        SeriesRequest("orders", WINDOW),
        SeriesRequest("ad_spend", WINDOW, dimension="ad_platform"),
        SeriesRequest("ad_spend", WINDOW),
    ]
    results = await series_adapter.fetch(requests)
    assert [r.request for r in results] == requests
    assert await series_adapter.fetch([]) == []


async def test_series_port_returns_only_days_inside_the_window_and_fingerprints_what_it_returns(series_adapter):
    [plain, split] = await series_adapter.fetch(
        [SeriesRequest("orders", WINDOW), SeriesRequest("ad_spend", WINDOW, dimension="ad_platform")]
    )
    assert plain.error is None and plain.values and all(WINDOW.contains(d) for d in plain.values)
    assert plain.fingerprint == result_fingerprint(plain.request, plain.values, plain.segments) and plain.segments == {}
    assert split.error is None and split.values == {} and split.segments
    assert all(WINDOW.contains(d) for s in split.segments.values() for d in s)
    assert split.fingerprint == result_fingerprint(split.request, split.values, split.segments)
    assert plain.source_query != ""


async def test_series_port_never_returns_numbers_with_an_error(series_adapter):
    results = await series_adapter.fetch([
        SeriesRequest("no_such_metric", WINDOW),
        SeriesRequest("orders", WINDOW, Scope.of({"not_a_dimension": "x"})),
    ])
    assert all(r.error for r in results)
    assert all(r.values == {} and r.segments == {} for r in results)


async def test_series_port_fingerprints_every_answer_by_the_one_rule_even_when_it_is_empty_or_an_error(series_adapter):
    long_before = Window(date(2020, 1, 1), date(2020, 1, 10))  # no data in this window
    results = await series_adapter.fetch([
        SeriesRequest("ad_spend", long_before, dimension="ad_platform"),
        SeriesRequest("ad_spend", long_before),
        SeriesRequest("no_such_metric", WINDOW),
        SeriesRequest("no_such_metric", WINDOW, dimension="ad_platform"),
    ])
    for r in results:
        assert r.fingerprint == result_fingerprint(r.request, r.values, r.segments), r.request


async def test_series_port_is_repeatable(series_adapter):
    request = SeriesRequest("ad_spend", WINDOW)
    [first] = await series_adapter.fetch([request])
    [second] = await series_adapter.fetch([request])
    assert first.fingerprint == second.fingerprint and dict(first.values) == dict(second.values)


def test_graph_port_returns_a_consistent_loaded_graph():
    loaded = VendoredGraphAdapter().load()
    assert isinstance(loaded, LoadedGraph)
    graph = loaded.graph
    assert graph.version and graph.nodes and graph.edges
    assert all(e.source in graph.nodes and e.target in graph.nodes for e in graph.edges)  # no dangling edge
    assert all(n.tier in graph.tiers for n in graph.nodes.values())
    assert VendoredGraphAdapter().load().graph == graph  # loading twice gives the same graph


async def test_evidence_port_shape_with_an_in_memory_stand_in():
    class Levers:
        kind = "lever"

        async def events(self, scope: Scope, window: Window) -> list[DatedEvidence]:
            return [DatedEvidence("lever", date(2026, 9, 12), "campaign x", "budget cut", "test")]

    found = await Levers().events(Scope(), WINDOW)
    assert all(WINDOW.contains(e.day) and e.kind == Levers.kind for e in found)

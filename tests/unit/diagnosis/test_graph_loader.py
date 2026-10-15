"""Graph loader (plan P1.3): the vendored graph loads; bad parts become issues, a bad document is rejected."""

from __future__ import annotations

import copy
import json

import pytest

from seleric_swarm.diagnosis.adapters.catalogue_graph import VENDORED_GRAPH, VendoredGraphAdapter
from seleric_swarm.diagnosis.domain.errors import GraphRejected
from seleric_swarm.diagnosis.domain.graph import (
    ACCOUNTING_KINDS,
    CatalogueFacts,
    Direction,
    EdgeKind,
)
from seleric_swarm.diagnosis.graph.loader import load_graph


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(VENDORED_GRAPH.read_text(encoding="utf-8"))


def _facts(document: dict) -> dict[str, CatalogueFacts]:
    return {
        n["id"]: CatalogueFacts(n["aggregation"], n["unit"], n["grain"], n["view"]) for n in document["nodes"]
    }


def test_vendored_graph_loads_with_its_declared_totals(document):
    loaded = VendoredGraphAdapter().load()
    graph = loaded.graph
    assert (len(graph.nodes), len(graph.edges)) == (233, 464)
    counts = {k: sum(1 for e in graph.edges if e.kind.value == k) for k in document["counts"]}
    assert counts == document["counts"]
    # same digest the eval fixture recorded for this graph
    assert graph.version == "3116740b8d70"
    assert sorted(graph.tiers) == list(range(11)) and graph.tiers[1].name == "spend"
    assert {"geo", "traffic", "product", "ad", "campaign"} == set(graph.hierarchies)
    assert loaded.excluded_nodes == ()
    assert {i.code for i in loaded.issues} == {"node_status_broken"}


def test_edges_are_typed_by_kind(document):
    graph = load_graph(document).graph
    mech = [e for e in graph.edges if e.kind is EdgeKind.MECHANISM]
    assert mech and all(e.direction in set(Direction) and e.lag_days and e.lag_days[0] <= e.lag_days[1] for e in mech)
    comp = [e for e in graph.edges if e.kind is EdgeKind.COMPOSITION]
    assert comp and {e.coefficient for e in comp} <= {1, -1}
    some = mech[0]
    assert some in graph.edges_into(some.target, frozenset({EdgeKind.MECHANISM}))
    assert some in graph.edges_from(some.source)
    assert all(e.kind in ACCOUNTING_KINDS for e in graph.edges_into("net_sales", ACCOUNTING_KINDS))


def test_unsupported_schema_is_rejected(document):
    bad = {**document, "version": 2}
    with pytest.raises(GraphRejected) as caught:
        load_graph(bad)
    assert caught.value.reason_code == "unsupported_schema"


def test_counts_that_disagree_with_the_edges_reject_the_document(document):
    bad = copy.deepcopy(document)
    bad["edges"].pop()
    with pytest.raises(GraphRejected) as caught:
        load_graph(bad)
    assert caught.value.reason_code == "edge_counts_disagree"


def test_builder_errors_reject_the_document(document):
    bad = {**document, "validation": {"errors": ["cycle in composition"], "warnings": []}}
    with pytest.raises(GraphRejected) as caught:
        load_graph(bad)
    assert caught.value.reason_code == "builder_errors"


def test_an_unknown_edge_kind_is_an_issue_not_a_crash(document):
    bad = copy.deepcopy(document)
    bad["edges"][0]["kind"] = "teleportation"
    bad["counts"] = {**bad["counts"], "teleportation": 1, bad_kind(document): bad["counts"][bad_kind(document)] - 1}
    loaded = load_graph(bad)
    assert len(loaded.graph.edges) == 463
    assert [i.code for i in loaded.issues if i.code != "node_status_broken"] == ["unknown_edge_kind"]


def bad_kind(document: dict) -> str:
    return document["edges"][0]["kind"]


def test_malformed_and_dangling_edges_are_set_aside_with_a_reason(document):
    bad = copy.deepcopy(document)
    mech = next(i for i, e in enumerate(bad["edges"]) if e["kind"] == "mechanism")
    bad["edges"][mech]["lag_days"] = [5, 1]
    dangling = next(i for i, e in enumerate(bad["edges"]) if e["kind"] == "twin")
    bad["edges"][dangling]["to"] = "no_such_metric"
    codes = [i.code for i in load_graph(bad).issues if i.code != "node_status_broken"]
    assert sorted(codes) == ["edge_unknown_node", "malformed_edge"]


def test_live_catalogue_drift_excludes_the_metric_and_its_edges(document):
    live = _facts(document)
    del live["sessions"]
    live["ad_spend"] = CatalogueFacts("ratio", "INR", "ad_day", "paid_media")
    loaded = load_graph(document, live=live, catalogue_version="abc")
    assert set(loaded.excluded_nodes) == {"sessions", "ad_spend"}
    assert "sessions" not in loaded.graph.nodes and loaded.graph.edges_into("sessions") == ()
    assert all("ad_spend" not in (e.source, e.target) for e in loaded.graph.edges)
    assert all("ad_spend" not in n.same_quantity_group for n in loaded.graph.nodes.values())
    by_code = {i.code: i.subject for i in loaded.issues if i.code != "node_status_broken"}
    assert by_code == {"node_not_in_catalogue": "sessions", "node_definition_drift": "ad_spend"}
    assert loaded.graph.catalogue_version == "abc"


def test_a_matching_catalogue_changes_nothing_but_the_stamp(document):
    plain = load_graph(document).graph
    checked = load_graph(document, live=_facts(document), catalogue_version="1635beb77932").graph
    assert checked.nodes == plain.nodes and checked.edges == plain.edges
    assert (plain.catalogue_version, checked.catalogue_version) == ("", "1635beb77932")


def test_an_empty_live_catalogue_is_a_failed_fetch_not_an_empty_catalogue(document):
    with pytest.raises(GraphRejected) as caught:
        load_graph(document, live={})
    assert caught.value.reason_code == "catalogue_empty"


FIXTURE = VENDORED_GRAPH.parents[4] / "eval" / "diag" / "fixtures" / "brand20_daily_2026-10.json"


def test_the_graph_agrees_with_the_catalogue_definitions_stored_with_the_eval_fixture(document):
    from seleric_swarm.diagnosis.adapters.catalogue_graph import facts_from_definitions

    definitions = json.loads(FIXTURE.read_text(encoding="utf-8"))["definitions"]
    facts = facts_from_definitions(definitions)
    assert len(facts) == len(definitions) == 81  # every definition carries the four facts, view included
    loaded = load_graph(document, live=facts, catalogue_version="1635beb77932")
    drift = [i for i in loaded.issues if i.code == "node_definition_drift"]
    assert drift == [], drift
    # the fixture is a subset of the catalogue: exactly the graph metrics it does not hold are 'not in catalogue'
    assert set(loaded.excluded_nodes) == {n["id"] for n in document["nodes"]} - set(facts)
    assert len(loaded.graph.nodes) == 81


def test_a_definition_missing_a_fact_gets_no_entry():
    from seleric_swarm.diagnosis.adapters.catalogue_graph import facts_from_definitions

    good = {"aggregation": "additive", "unit": "INR", "grain": "ad_day", "cube_mapping": {"view": "paid_media"}}
    got = facts_from_definitions({"a": good, "b": {**good, "cube_mapping": {}}, "c": {**good, "unit": None}})
    assert list(got) == ["a"]


def test_renames_bridge_a_catalogue_that_changed_its_ids(document):
    plain = load_graph(document).graph
    renamed = load_graph(document, renames={"ad_spend": "paid_spend"}).graph
    assert "ad_spend" not in renamed.nodes and "paid_spend" in renamed.nodes
    assert len(renamed.edges) == len(plain.edges)
    assert all("ad_spend" not in (e.source, e.target) for e in renamed.edges)
    assert all("ad_spend" not in n.same_quantity_group for n in renamed.nodes.values())
    assert any(e.source == "paid_spend" for e in renamed.edges)
    assert renamed.version == plain.version  # still the document as written
    ratio_with_spend = [n for n in plain.nodes.values() if n.ratio and "ad_spend" in (n.ratio.numerator, n.ratio.denominator)]
    for n in ratio_with_spend:
        assert "ad_spend" not in (renamed.nodes[n.id].ratio.numerator, renamed.nodes[n.id].ratio.denominator)  # type: ignore[union-attr]
    # a renamed id is then looked up in the catalogue under its new name
    live = _facts(document)
    live["paid_spend"] = live.pop("ad_spend")
    assert load_graph(document, live=live, renames={"ad_spend": "paid_spend"}).excluded_nodes == ()


def test_two_metrics_renamed_to_one_id_are_refused(document):
    with pytest.raises(GraphRejected) as caught:
        load_graph(document, renames={"ad_spend": "x", "orders": "x"})
    assert caught.value.reason_code == "rename_collision"

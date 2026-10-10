"""Metric graph with an invented catalogue."""

from __future__ import annotations

from seleric_swarm.graph import build_graph
from seleric_swarm.semantic import SemanticReadModel

MODEL = SemanticReadModel.from_payload({
    "catalogue_version": "zz-v1",
    "metrics": [
        {"id": "zz_take"}, {"id": "zz_count"}, {"id": "zz_visits"}, {"id": "zz_refunds"},
        {"id": "zz_avg", "composition": {"numerator": "zz_take", "denominator": "zz_count"}},
        {"id": "zz_net", "composition": {"terms": [{"metric": "zz_take"}, {"metric": "zz_refunds", "coefficient": -1}]}},
        {"id": "zz_rate", "composition": {"depends_on": ["zz_visits", "zz_gone"]}},
    ],
    "dimensions": [{"id": "zz_zone"}],
})
CAUSAL = {"graphs": [{"id": "g1", "edges": [
    ["zz_visits", "zz_count", 1, 0],
    {"source": "zz_zone", "target": "zz_visits", "lag_days": 2},
    ["zz_unknown", "zz_take"],
    "bad",
]}]}


def test_identity_edges_from_composition() -> None:
    g = build_graph(MODEL)
    assert g.components("zz_avg") == ("zz_take", "zz_count")
    assert {(e.source, e.coefficient) for e in g.parents("zz_net")} == {("zz_take", 1.0), ("zz_refunds", -1.0)}
    assert g.components("zz_rate") == ("zz_visits",)  # unknown component dropped


def test_causal_edges_and_skips() -> None:
    g = build_graph(MODEL, CAUSAL)
    assert [(e.source, e.sign) for e in g.causes("zz_count")] == [("zz_visits", 1)]
    assert g.causes("zz_visits")[0].lag_days == 2
    assert [(s.node, s.graph_id) for s in g.skipped] == [("zz_unknown", "g1")]
    assert not g.causes("zz_take")


def test_ancestors_walk_both_kinds() -> None:
    g = build_graph(MODEL, CAUSAL)
    assert set(g.ancestors("zz_avg")) == {"zz_take", "zz_count", "zz_visits", "zz_zone"}


def test_renamed_metric_drops_its_edges() -> None:
    renamed = SemanticReadModel.from_payload({"metrics": [{"id": "zz_count2"}, {"id": "zz_visits"}]})
    g = build_graph(renamed, CAUSAL)
    assert not g.edges and {s.node for s in g.skipped} >= {"zz_count", "zz_zone"}

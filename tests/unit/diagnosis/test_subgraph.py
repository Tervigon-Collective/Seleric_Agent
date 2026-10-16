"""The candidate subgraph (plan P3.1): what the reviewed graph says about an outcome, built by the edge-kind policies,
frozen in the plan, shown as events, and carried by the record. Synthetic graphs prove the rules; the vendored graph
pins the facts the Phase 3 design rests on."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime

import pytest

from seleric_swarm.diagnosis.adapters.catalogue_graph import VendoredGraphAdapter
from seleric_swarm.diagnosis.domain.gather import GatherPlan
from seleric_swarm.diagnosis.domain.graph import (
    SAME_QUANTITY_KINDS,
    Direction,
    Edge,
    EdgeKind,
    Graph,
    Node,
)
from seleric_swarm.diagnosis.domain.subgraph import Subgraph, confidence_rank
from seleric_swarm.diagnosis.graph.edge_policy import (
    ACCOUNT,
    CAUSE,
    EXCLUDE,
    MEASUREMENT,
)
from seleric_swarm.diagnosis.graph.policies import register_edge_policies
from seleric_swarm.diagnosis.graph.subgraph import build_subgraph
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.emitter import DEFAULT_PAYLOAD_CAP_BYTES, Emitter, MemorySink
from seleric_swarm.diagnosis.trace.graph_events import emit_subgraph


def _node(i: str, tier: int = 5) -> Node:
    return Node(i, i, "v", "c", "sum", "n", "day", "d", "certified", tier, False, "", None, ())


def _edge(source: str, target: str, kind: EdgeKind, confidence: str = "strong", lag: tuple[int, int] | None = None,
          direction: Direction | None = None) -> Edge:
    if kind in (EdgeKind.MECHANISM, EdgeKind.FEEDBACK):
        lag, direction = lag or (0, 0), direction or Direction.POSITIVE
    return Edge(source, target, kind, "test", confidence, lag_days=lag, direction=direction, expected="agree")


def _graph(nodes: list[str], edges: list[Edge], tiers: dict[str, int] | None = None) -> Graph:
    tiers = tiers or {}
    return Graph(
        1, "test-graph", "", {n: _node(n, tiers.get(n, 5)) for n in nodes}, tuple(edges), {}, {}, {},
    )


def _ids(subgraph: Subgraph) -> list[str]:
    return [c.metric for c in subgraph.candidates]


def test_the_shipped_policies_cover_every_edge_kind_and_say_what_the_kind_means():
    registry = register_edge_policies()
    assert set(registry.kinds()) == {k.value for k in EdgeKind}
    assert register_edge_policies() is registry  # safe to call again
    graph = VendoredGraphAdapter().load().graph
    decided = {}
    for edge in graph.edges:
        decided.setdefault(edge.kind, set()).add(registry.get(edge.kind.value)().decide(edge, graph).action)
    assert decided[EdgeKind.COMPOSITION] == decided[EdgeKind.RATIO_COMPONENT] == decided[EdgeKind.DECLARED_INPUT] == {ACCOUNT}
    assert decided[EdgeKind.MECHANISM] == {CAUSE} and decided[EdgeKind.CO_MEASURE] == {MEASUREMENT}
    assert decided[EdgeKind.TWIN] == decided[EdgeKind.SUBSET] == decided[EdgeKind.ALLOCATION] == {EXCLUDE}
    assert decided[EdgeKind.FEEDBACK] == {CAUSE}  # the graph's three feedback edges all react after a day or more


def test_importing_the_builder_registers_no_policy():
    code = (
        "import seleric_swarm.diagnosis.graph.subgraph as g, seleric_swarm.diagnosis.graph.edge_policy as e; "
        "print(e.EDGE_POLICIES.kinds())"
    )
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "()"


def test_the_accounting_tree_is_walked_and_never_offered_as_a_cause():
    graph = _graph(
        ["y", "a", "b", "c", "u"],
        [_edge("a", "y", EdgeKind.COMPOSITION), _edge("b", "y", EdgeKind.RATIO_COMPONENT), _edge("c", "a", EdgeKind.DECLARED_INPUT),
         _edge("u", "a", EdgeKind.MECHANISM)],
    )
    sub = build_subgraph(graph, "y")
    assert sub.accounting == ("a", "b", "c")
    assert _ids(sub) == ["u"] and sub.candidates[0].via == "a" and sub.candidates[0].anchor == "a"


def test_the_walk_goes_through_the_components_because_no_edge_targets_the_outcome():
    graph = _graph(
        ["y", "part", "mid", "root"],
        [_edge("part", "y", EdgeKind.COMPOSITION), _edge("mid", "part", EdgeKind.MECHANISM), _edge("root", "mid", EdgeKind.MECHANISM)],
    )
    sub = build_subgraph(graph, "y")
    assert [(c.metric, c.distance, c.via) for c in sub.candidates] == [("mid", 1, "part"), ("root", 2, "mid")]


def test_a_metric_reached_twice_keeps_the_nearest_path_and_among_equals_the_strongest():
    graph = _graph(
        ["y", "near", "weak", "strong", "x"],
        [_edge("near", "y", EdgeKind.MECHANISM), _edge("x", "near", EdgeKind.MECHANISM),
         _edge("x", "y", EdgeKind.MECHANISM, "moderate"),  # shorter, weaker
         _edge("weak", "y", EdgeKind.MECHANISM, "moderate"), _edge("strong", "y", EdgeKind.MECHANISM, "strong"),
         _edge("strong", "weak", EdgeKind.MECHANISM, "strong")],
    )
    sub = build_subgraph(graph, "y")
    by = {c.metric: c for c in sub.candidates}
    assert by["x"].distance == 1 and by["x"].via == "y" and by["x"].path_confidence == "moderate"
    assert by["strong"].distance == 1 and by["strong"].path_confidence == "strong"


def test_a_path_is_as_strong_as_its_weakest_edge_and_its_lag_and_sign_accumulate():
    graph = _graph(
        ["y", "m", "r"],
        [_edge("m", "y", EdgeKind.MECHANISM, "structural", (0, 1), Direction.NEGATIVE),
         _edge("r", "m", EdgeKind.MECHANISM, "moderate", (2, 3), Direction.NEGATIVE)],
    )
    r = {c.metric: c for c in build_subgraph(graph, "y").candidates}["r"]
    assert (r.path_confidence, r.lag_days, r.direction, r.distance) == ("moderate", (2, 4), "+", 2)
    assert confidence_rank("structural") > confidence_rank("strong") > confidence_rank("moderate") > confidence_rank("other")


def test_candidates_are_offered_root_first_and_a_near_identity_is_walked_through_not_offered():
    graph = _graph(
        ["y", "a", "mid", "lever", "late", "part", "identity"],
        [_edge("a", "y", EdgeKind.MECHANISM), _edge("mid", "a", EdgeKind.MECHANISM), _edge("lever", "mid", EdgeKind.MECHANISM),
         _edge("late", "y", EdgeKind.MECHANISM), _edge("part", "late", EdgeKind.MECHANISM, "structural"),
         _edge("identity", "a", EdgeKind.MECHANISM, "structural"), _edge("lever", "identity", EdgeKind.MECHANISM)],
        tiers={"lever": 1, "mid": 3, "a": 4, "late": 4, "part": 6, "identity": 6},
    )
    sub = build_subgraph(graph, "y")
    assert _ids(sub) == ["lever", "mid", "a", "late"]  # tier first (most upstream), then nearest, then id
    assert [(e.metric, e.reason_code, e.check_value) for e in sub.exclusions] == [
        ("identity", "near_identity_of_mechanism", "a"), ("part", "near_identity_of_mechanism", "late"),
    ]
    assert {c.metric: c.distance for c in sub.candidates}["lever"] == 3  # reached through `mid` and through `identity`


def test_an_outcome_the_graph_does_not_cover_gets_no_subgraph_and_the_tool_falls_back():
    from seleric_swarm.diagnosis.adapters.live_subgraph import plan_subgraph, subgraph_drivers
    from seleric_swarm.diagnosis.domain.graph import LoadedGraph

    loaded = VendoredGraphAdapter().load()
    assert plan_subgraph(loaded, "no_such_metric", {}, (), lambda m: True) is None
    assert plan_subgraph(None, "net_sales", {}, (), lambda m: True) is None
    sub = build_subgraph(loaded.graph, "net_sales")
    assert subgraph_drivers(sub, 3) == [c.metric for c in sub.candidates[:3]] and isinstance(loaded, LoadedGraph)


def test_a_feedback_edge_counts_only_when_the_reaction_takes_a_day():
    graph = _graph(
        ["y", "slow", "same_day"],
        [_edge("slow", "y", EdgeKind.FEEDBACK, lag=(1, 7)), _edge("same_day", "y", EdgeKind.FEEDBACK, lag=(0, 1))],
    )
    sub = build_subgraph(graph, "y")
    assert _ids(sub) == ["slow"]
    assert [(e.metric, e.reason_code) for e in sub.exclusions] == [("same_day", "feedback_without_lag")]


def test_a_metric_that_cannot_be_offered_is_walked_through_and_set_aside_with_its_reason():
    graph = _graph(["y", "gate", "behind"], [_edge("gate", "y", EdgeKind.MECHANISM), _edge("behind", "gate", EdgeKind.MECHANISM)])
    sub = build_subgraph(graph, "y", usable=lambda m: "not_additive" if m == "gate" else None)
    assert _ids(sub) == ["behind"] and sub.candidates[0].via == "gate"
    assert [(e.metric, e.reason_code) for e in sub.exclusions] == [("gate", "not_additive")]


@pytest.mark.parametrize(
    ("kind", "reason", "action"),
    [
        (EdgeKind.TWIN, "twin_of_outcome", "exclude"),
        (EdgeKind.SUBSET, "subset_of_outcome", "exclude"),
        (EdgeKind.ALLOCATION, "allocation_of_outcome", "exclude"),
        (EdgeKind.CO_MEASURE, "co_measure_of_outcome", "measurement_hypothesis"),
    ],
)
def test_the_same_quantity_as_the_outcome_or_a_part_of_it_is_never_a_cause(kind, reason, action):
    # `same` is reached as a cause of `part` (a part of the outcome) and is the outcome's twin/co-measure/subset/allocation
    graph = _graph(
        ["y", "part", "same", "real"],
        [_edge("part", "y", EdgeKind.COMPOSITION), _edge("same", "part", EdgeKind.MECHANISM), _edge("real", "part", EdgeKind.MECHANISM),
         _edge("same", "part", kind)],
    )
    sub = build_subgraph(graph, "y")
    assert _ids(sub) == ["real"]
    [exclusion] = sub.exclusions
    assert (exclusion.metric, exclusion.reason_code, exclusion.check_value, exclusion.action) == ("same", reason, "part", action)


def test_a_metric_computed_from_the_outcome_follows_it_and_cannot_cause_it():
    graph = _graph(
        ["y", "ratio", "driver"],
        [_edge("y", "ratio", EdgeKind.RATIO_COMPONENT), _edge("ratio", "driver", EdgeKind.MECHANISM),
         _edge("ratio", "y", EdgeKind.FEEDBACK, lag=(1, 3))],
    )
    sub = build_subgraph(graph, "y")
    assert _ids(sub) == []
    assert [(e.metric, e.reason_code, e.check_value) for e in sub.exclusions] == [("ratio", "computed_from_outcome", "y")]


def test_the_other_measurements_of_the_outcome_and_its_parts_are_listed_as_peers():
    graph = _graph(
        ["y", "part", "y_twin", "part_other", "elsewhere"],
        [_edge("part", "y", EdgeKind.COMPOSITION), _edge("y_twin", "y", EdgeKind.TWIN), _edge("part", "part_other", EdgeKind.CO_MEASURE),
         _edge("elsewhere", "part_other", EdgeKind.TWIN)],
    )
    sub = build_subgraph(graph, "y")
    assert [(p.metric, p.peer, p.kind, p.expected) for p in sub.peers] == [
        ("part", "part_other", "co_measure", "agree"), ("y", "y_twin", "twin", "agree"),
    ]


def test_cycles_end_and_the_depth_limit_holds():
    graph = _graph(
        ["y", "a", "b", "c"],
        [_edge("a", "y", EdgeKind.MECHANISM), _edge("b", "a", EdgeKind.MECHANISM), _edge("c", "b", EdgeKind.MECHANISM),
         _edge("a", "c", EdgeKind.MECHANISM)],
    )
    assert _ids(build_subgraph(graph, "y")) == ["a", "b", "c"]
    shallow = build_subgraph(graph, "y", max_depth=2)
    assert _ids(shallow) == ["a", "b"] and shallow.depth_limit == 2


def test_the_subgraph_is_deterministic_and_survives_its_text_form():
    graph = VendoredGraphAdapter().load().graph
    first, second = build_subgraph(graph, "net_roas"), build_subgraph(graph, "net_roas")
    assert first == second and first.digest == second.digest
    assert Subgraph.from_dict(json.loads(json.dumps(first.to_dict()))) == first
    assert first.graph_version == graph.version


def test_the_vendored_graph_reaches_the_planted_causes_only_through_the_components():
    graph = VendoredGraphAdapter().load().graph
    assert not graph.edges_into("net_sales", frozenset({EdgeKind.MECHANISM}))  # why a walk of the outcome's own edges is empty
    sub = build_subgraph(graph, "net_sales")
    by = {c.metric: c for c in sub.candidates}
    assert by["ad_spend"].distance == 6 and by["site_searches"].distance == 5 and by["sessions"].distance == 3
    assert not set(sub.accounting) & set(by)


def test_the_vendored_graph_names_the_funnel_purchases_orders_pair_for_the_measurement_check():
    graph = VendoredGraphAdapter().load().graph
    sub = build_subgraph(graph, "funnel_purchases")
    [orders] = [e for e in sub.exclusions if e.metric == "orders"]
    assert (orders.reason_code, orders.action, orders.check_value) == ("co_measure_of_outcome", "measurement_hypothesis", "funnel_purchases")
    assert "orders" not in _ids(sub)


def test_a_part_of_a_ratio_that_is_a_subset_of_a_part_is_not_offered_as_a_cause():
    graph = VendoredGraphAdapter().load().graph
    sub = build_subgraph(graph, "ctr")
    assert any(e.reason_code == "subset_of_outcome" for e in sub.exclusions)
    assert not {e.metric for e in sub.exclusions} & set(_ids(sub))


def test_the_plan_carries_the_subgraph_and_an_old_plan_without_one_still_reads():
    graph = VendoredGraphAdapter().load().graph
    sub = build_subgraph(graph, "net_sales")
    from seleric_swarm.diagnosis.domain.lineage import MetricMeta

    plan = GatherPlan(
        outcome="net_sales", start=date(2026, 9, 1), end=date(2026, 9, 29), scope={}, event_days=(date(2026, 9, 29),),
        partial_days=frozenset(), baseline_days=(), claimed_direction=None, lineage={"net_sales": MetricMeta("net_sales")},
        series_ids=("net_sales",), core=frozenset(), change_log=frozenset(), ladder_ends=frozenset(), bridge_pool=(),
        drivers=(), dims=(), chain_dims=(), scope_tokens=frozenset(), subgraph=sub,
    )
    again = GatherPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert again.subgraph == sub and again == plan
    old = plan.to_dict()
    del old["subgraph"]
    assert GatherPlan.from_dict(old).subgraph is None
    assert "subgraph" not in GatherPlan.from_dict(old).to_dict()  # an old plan keeps its old text, so its record fingerprint holds


def test_the_events_are_small_and_the_exclusions_hang_under_the_summary():
    graph = VendoredGraphAdapter().load().graph
    sub = build_subgraph(graph, "net_roas")  # the largest of the outcomes checked
    sink = MemorySink()
    emitter = Emitter("inv_t", lambda: datetime(2026, 10, 16, tzinfo=UTC), [sink])
    emit_subgraph(emitter, sub)
    built = [e for e in sink.events if e.kind == kinds.SUBGRAPH_BUILT]
    assert len(built) == 1 and len(json.dumps(built[0].payload)) < DEFAULT_PAYLOAD_CAP_BYTES
    assert not emitter.overflow  # nothing was cut down to a stub
    assert built[0].payload["digest"] == sub.digest and built[0].payload["candidates"] == _ids(sub)
    excluded = [e for e in sink.events if e.kind == kinds.EXCLUSION]
    assert [e.payload["metric"] for e in excluded] == [e.metric for e in sub.exclusions]
    assert {e.parent_id for e in excluded} == {built[0].seq}


# ---- the live tool: the subgraph is frozen in the plan, shown on the trace, and changes nothing else (shadow mode)
async def _diagnose(cube_world, metric="net_sales"):
    from datetime import timedelta

    from seleric_swarm.agent.progress import clear_progress_sink, set_progress_sink
    from seleric_swarm.diagnosis.adapters.runstore_artifacts import ARTIFACT_TYPE
    from seleric_swarm.diagnosis.domain.record import RunRecord
    from seleric_swarm.toolsets import diagnosis

    ctx, _ = cube_world
    diagnosis._definitions_cache.update({"at": 0.0, "ids": frozenset(), "defs": {}})
    seen: list[tuple[str, dict]] = []
    set_progress_sink("t", lambda kind, summary, payload: seen.append((kind, payload)))
    start = datetime(2026, 9, 27, tzinfo=UTC)
    try:
        result = await diagnosis.diagnose_metric_change(ctx, metric, event_start=start, event_end=start + timedelta(days=2))
    finally:
        clear_progress_sink("t")
    mine = result.provenance.source_metadata["investigation_id"]
    [artifact] = [
        a for a in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id)
        if a.artifact_type == ARTIFACT_TYPE and a.payload["investigation_id"] == mine
    ]
    return result, RunRecord.from_dict(artifact.payload), [p["investigation"] for k, p in seen if k.startswith("diag.")]


async def test_a_live_diagnosis_freezes_the_subgraph_offers_its_candidates_and_shows_it_on_the_trace(cube_world, monkeypatch):
    from seleric_swarm.diagnosis.trace.run import replay
    from seleric_swarm.toolsets import diagnosis

    result, record, live = await _diagnose(cube_world)
    assert result.success
    plan = GatherPlan.from_dict(record.request["plan"])
    assert plan.subgraph is not None and plan.subgraph.outcome == "net_sales" and plan.drivers_from_graph
    built = [e for e in record.events if e["kind"] == kinds.SUBGRAPH_BUILT]
    assert [e["payload"]["digest"] for e in built] == [plan.subgraph.digest]
    assert built[0]["payload"]["graph_version"] == "3116740b8d70"
    assert [e["payload"] for e in live if e["kind"] == kinds.SUBGRAPH_BUILT] == [e["payload"] for e in built]
    # what the catalogue lacks and the engine cannot estimate is set aside with a reason, never silently dropped
    assert {e.reason_code.removeprefix("near_identity_of_") for e in plan.subgraph.exclusions} <= {
        "not_in_catalogue", "not_additive", "arithmetic_part_of_outcome", "feedback_without_lag", "mechanism",
    }
    assert all(c.metric in {m.id for m in cube_world[0].deps.catalogue.metrics} for c in plan.subgraph.candidates)
    assert (await replay(record)).identical

    # the drivers the engine is given are the subgraph's candidates in its order (D-51), none of them a near-identity
    # of what it feeds; without a subgraph the tool falls back to the catalogue's lineage and says so
    cap = diagnosis.P.DIAG_MAX_DRIVERS
    assert plan.drivers[:cap] == tuple(c.metric for c in plan.subgraph.candidates[:cap])
    assert [c.tier for c in plan.subgraph.candidates] == sorted(c.tier for c in plan.subgraph.candidates)
    assert all(c.edge_confidence != "structural" for c in plan.subgraph.candidates)
    monkeypatch.setattr(diagnosis, "plan_subgraph", lambda *args, **kwargs: None)
    bare_result, bare_record, _ = await _diagnose(cube_world)
    assert bare_result.success and "subgraph" not in bare_record.request["plan"]
    assert "candidate causes were chosen from the catalogue's lineage, not the reviewed graph" in bare_result.provenance.source_metadata["diagnosis"]["data_quality"]
    assert not [e for e in bare_record.events if e["kind"] == kinds.SUBGRAPH_BUILT]
    assert (await replay(bare_record)).identical  # a plan without a subgraph replays as before


# ---- explain and diff read a record and say what happened to a metric / what changed between two runs
async def test_explain_says_why_a_metric_was_or_was_not_a_candidate_and_what_became_of_it(cube_world, capsys, tmp_path):
    from seleric_swarm.diagnosis.cli import explain

    _, record, _ = await _diagnose(cube_world)
    assert record.subgraph == record.request["plan"]["subgraph"]
    plan_sub = Subgraph.from_dict(record.subgraph)
    offered = next(c.metric for c in plan_sub.candidates if c.metric in record.request["plan"]["drivers"])
    text = "\n".join(explain.explain_metric(record, offered))
    assert "candidate cause #" in text and "offered to the engine as a driver" in text and "engine:" in text
    part = plan_sub.accounting[0]
    assert "arithmetic" in "\n".join(explain.explain_metric(record, part))
    assert "not reached" in "\n".join(explain.explain_metric(record, "no_such_metric"))
    assert "outcome itself" in "\n".join(explain.explain_metric(record, "net_sales"))
    path = tmp_path / "record.json"
    path.write_text(record.to_json(), encoding="utf-8")
    assert explain.main([str(path), "--metric", offered]) == 0 and offered in capsys.readouterr().out
    assert explain.main([str(tmp_path / "missing.json"), "--metric", offered]) == 2


def test_explain_names_the_reason_a_metric_was_set_aside():
    from seleric_swarm.diagnosis.cli import explain
    from seleric_swarm.diagnosis.domain.record import RunRecord

    graph = VendoredGraphAdapter().load().graph
    sub = build_subgraph(graph, "funnel_purchases")
    record = RunRecord.from_dict({
        "schema_version": 1, "investigation_id": "inv_x", "created_at": "2026-10-16T00:00:00+00:00", "runner": "r", "request": {},
        "versions": {}, "config": {}, "seed": 0, "subgraph": sub.to_dict(), "estimates": [], "snapshots": [], "blobs": {},
        "events": [], "overflow": {}, "runtime_trace": {}, "report": None,
    })
    lines = explain.explain_metric(record, "orders")
    assert any("co_measure_of_outcome" in l and "measurement_hypothesis" in l and "funnel_purchases" in l for l in lines)
    assert any("another measurement of funnel_purchases (co_measure" in l for l in lines)


async def test_diff_finds_the_first_section_that_differs(cube_world, monkeypatch, tmp_path, capsys):
    from dataclasses import replace

    from seleric_swarm.diagnosis.cli import diff
    from seleric_swarm.toolsets import diagnosis

    _, a, _ = await _diagnose(cube_world)
    assert diff.diff_records(a, a) == {}
    monkeypatch.setattr(diagnosis, "plan_subgraph", lambda *args, **kwargs: None)
    _, b, _ = await _diagnose(cube_world)
    sections = diff.diff_records(a, b)
    assert "subgraph: only in A" in sections["plan and subgraph"] and "subgraph_built: 1 != 0" in sections["events"]
    assert any(line.startswith("plan drivers") for line in sections["plan and subgraph"])  # the fallback offers other metrics
    c = replace(a, report={**(a.report or {}), "verdict": "changed"})
    assert any("verdict" in line for line in diff.diff_records(a, c)["report"])
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(a.to_json(), encoding="utf-8")
    pb.write_text(b.to_json(), encoding="utf-8")
    assert diff.main([str(pa), str(pa)]) == 0 and diff.main([str(pa), str(pb)]) == 1
    assert "plan and subgraph:" in capsys.readouterr().out
    assert diff.main([str(pa), str(tmp_path / "missing.json")]) == 2


async def test_a_subgraph_that_cannot_be_built_is_reported_and_does_not_fail_the_answer(cube_world, monkeypatch):
    from seleric_swarm.diagnosis.adapters import live_subgraph

    def broken(*args, **kwargs):
        raise RuntimeError("graph walk failed")

    monkeypatch.setattr(live_subgraph, "build_subgraph", broken)
    result, record, live = await _diagnose(cube_world)
    assert result.success and "subgraph" not in record.request["plan"] and record.subgraph == {}
    failed = [e["payload"] for e in live if e["kind"] == kinds.OPERATOR_FAILED and e["payload"]["operator"] == "build_subgraph"]
    assert failed and failed[0]["error_type"] == "RuntimeError"


async def test_a_metric_the_graph_gives_no_candidates_for_falls_back_to_the_lineage_and_says_so(cube_world):
    from seleric_swarm.diagnosis.trace.run import replay

    result, record, _ = await _diagnose(cube_world, "discounts")
    plan = GatherPlan.from_dict(record.request["plan"])
    assert result.success and plan.subgraph is not None and not plan.subgraph.candidates
    assert not plan.drivers_from_graph and plan.drivers  # the lineage proposed them
    assert "candidate causes were chosen from the catalogue's lineage, not the reviewed graph" in result.provenance.source_metadata["diagnosis"]["data_quality"]
    assert (await replay(record)).identical


def test_for_every_outcome_in_the_graph_no_candidate_is_the_same_quantity_as_it_or_a_part_of_it():
    """P3.3 on the whole vendored graph: an allocated (product) metric, a twin, a co-measure or a subset of the outcome or
    of anything that builds it is never offered as its cause, and every metric set aside carries a reason."""
    graph = VendoredGraphAdapter().load().graph
    for outcome in graph.nodes:
        sub = build_subgraph(graph, outcome)
        protected = {outcome, *sub.accounting}
        for c in sub.candidates:
            assert not graph.nodes[c.metric].allocated, (outcome, c.metric)
            same = [e for e in (*graph.edges_into(c.metric), *graph.edges_from(c.metric)) if e.kind in SAME_QUANTITY_KINDS]
            assert not [e for e in same if {e.source, e.target} & protected - {c.metric}], (outcome, c.metric)
        assert all(e.reason_code for e in sub.exclusions)

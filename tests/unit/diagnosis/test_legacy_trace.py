"""The live diagnosis tool emits a trace (plan P1.5b / P1.6b): ordered events, one correlation id, and no change to
what it answers. The semantic layer is the eval world; the tool is the real one."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from seleric_swarm.agent.progress import clear_progress_sink, set_progress_sink
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.correlation import current_investigation_id
from seleric_swarm.toolsets import diagnosis

START, END = datetime(2026, 9, 27, tzinfo=UTC), datetime(2026, 9, 29, tzinfo=UTC)


def _fresh(ctx):
    diagnosis._definitions_cache.update({"at": 0.0, "ids": frozenset(), "defs": {}})
    return ctx


async def _run(cube_world, **kw):
    ctx, _ = cube_world
    seen: list[tuple[str, dict]] = []
    set_progress_sink("t", lambda kind, summary, payload: seen.append((kind, payload)))
    try:
        result = await diagnosis.diagnose_metric_change(_fresh(ctx), "net_sales", event_start=START, event_end=END, **kw)
    finally:
        clear_progress_sink("t")
    envelopes = [p["investigation"] for k, p in seen if k.startswith("diag.")]
    return result, envelopes


async def test_a_diagnosis_leaves_an_ordered_trace_with_one_id(cube_world, caplog):
    with caplog.at_level(logging.INFO):
        result, events = await _run(cube_world)
    assert result.success
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert {e["investigation_id"] for e in events} == {result.provenance.source_metadata["investigation_id"]}
    kinds_seen = [e["kind"] for e in events]
    assert kinds_seen[0] == kinds.INVESTIGATION_STARTED and kinds_seen[-1] == kinds.INVESTIGATION_FINISHED
    for needed in (kinds.WINDOW_RESOLVED, kinds.GRAPH_LOADED, kinds.QUERY_ISSUED, kinds.HYPOTHESIS_RESOLVED):
        assert needed in kinds_seen, needed
    assert kinds_seen.index(kinds.WINDOW_RESOLVED) < kinds_seen.index(kinds.GRAPH_LOADED) < kinds_seen.index(kinds.QUERY_ISSUED)
    # every operator that starts also finishes (or fails), in nesting order
    stack: list[str] = []
    for e in events:
        if e["kind"] == kinds.OPERATOR_STARTED:
            stack.append(e["payload"]["operator"])
        elif e["kind"] in (kinds.OPERATOR_FINISHED, kinds.OPERATOR_FAILED):
            assert stack.pop() == e["payload"]["operator"]
    names = [e["payload"]["operator"] for e in events if e["kind"] == kinds.OPERATOR_STARTED]
    assert stack == [] and {"fetch_series", "analyse"} <= set(names)
    # the analysis is four stages inside "analyse", in the order the engine runs them
    assert [o for o in names if o in {"is_real", "what", "where", "why_event"}] == ["is_real", "what", "where", "why_event"]
    queries = [e for e in events if e["kind"] == kinds.QUERY_ISSUED]
    started = {e["seq"]: e["payload"]["operator"] for e in events if e["kind"] == kinds.OPERATOR_STARTED}
    owners = {started.get(q["parent_id"], "<none>") for q in queries}
    assert queries and owners <= {"fetch_series", "fetch_segments", "fetch_chain_segments", "levers"}, owners
    finished = events[-1]["payload"]
    assert finished["termination_reason"] == result.provenance.source_metadata["diagnosis"]["verdict"]
    assert finished["instrumentation_errors"] == 0 and finished["sink_failures"] == 0


async def test_the_graph_is_checked_against_the_live_catalogue_and_exclusions_are_events(cube_world):
    _, events = await _run(cube_world)
    [loaded] = [e for e in events if e["kind"] == kinds.GRAPH_LOADED]
    assert loaded["payload"]["graph_version"] == "3116740b8d70" and loaded["payload"]["catalogue_version"]
    excluded = [e for e in events if e["kind"] == kinds.EXCLUSION and e["parent_id"] == loaded["seq"]]
    # the eval world's catalogue is a subset of the graph's metrics, so the other graph metrics are reported as not in it
    assert len(excluded) == 233 - len(cube_world[0].deps.catalogue.metrics) and {e["payload"]["reason_code"] for e in excluded} == {"node_not_in_catalogue"}


async def test_log_lines_inside_the_call_carry_the_id_and_nothing_leaks_outside(cube_world, caplog):
    with caplog.at_level(logging.DEBUG):
        result, _ = await _run(cube_world)
    ids = {r.investigation_id for r in caplog.records if getattr(r, "investigation_id", "")}
    assert ids == {result.provenance.source_metadata["investigation_id"]}
    assert current_investigation_id() == ""


async def test_calling_the_body_directly_emits_nothing(cube_world):
    ctx, _ = cube_world
    seen: list[str] = []
    set_progress_sink("t", lambda kind, summary, payload: seen.append(kind))
    try:
        result = await diagnosis._diagnose(_fresh(ctx), "net_sales", event_start=START, event_end=END)
    finally:
        clear_progress_sink("t")
    assert result.success and seen == [] and "investigation_id" not in result.provenance.source_metadata


async def test_a_failing_event_stream_does_not_change_the_answer(cube_world):
    ctx, _ = cube_world
    plain = await diagnosis._diagnose(_fresh(ctx), "net_sales", event_start=START, event_end=END)

    def broken(kind, summary, payload):
        raise RuntimeError("stream down")

    set_progress_sink("t", broken)
    try:
        traced = await diagnosis.diagnose_metric_change(_fresh(ctx), "net_sales", event_start=START, event_end=END)
    finally:
        clear_progress_sink("t")
    assert traced.summary == plain.summary
    assert traced.provenance.source_metadata["diagnosis"] == plain.provenance.source_metadata["diagnosis"]

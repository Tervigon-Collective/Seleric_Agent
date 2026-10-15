"""Trace events (plan P1.5): ordered, validated, size-limited, replayable text, and visible through the stream."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from seleric_swarm.agent.progress import clear_progress_sink, set_progress_sink
from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.conversations.privacy import event_for_principal
from seleric_swarm.diagnosis.adapters.catalogue_graph import VENDORED_GRAPH, facts_from_definitions
from seleric_swarm.diagnosis.adapters.mission_events import MissionEventSink
from seleric_swarm.diagnosis.domain.events import Event
from seleric_swarm.diagnosis.graph.loader import load_graph
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.emitter import DEFAULT_PAYLOAD_CAP_BYTES, Emitter, MemorySink
from seleric_swarm.diagnosis.trace.graph_events import emit_graph_loaded

T0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)


def _clock():
    state = {"n": 0}

    def tick() -> datetime:
        state["n"] += 1
        return T0 + timedelta(seconds=state["n"])

    return tick


def _emitter(*extra):
    memory = MemorySink()
    return Emitter("inv-1", _clock(), [memory, *extra]), memory


def test_events_are_numbered_without_gaps_even_from_many_threads():
    emitter, memory = _emitter()

    def work() -> None:
        for _ in range(50):
            emitter.emit(kinds.NODE_VISITED, {"node": "n"})

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert [e.seq for e in memory.events] == list(range(1, 401))
    stamps = [e.ts for e in memory.events]
    assert stamps == sorted(stamps)  # time never runs backwards against seq


def test_an_event_missing_a_required_key_or_of_an_unknown_kind_is_refused():
    emitter, memory = _emitter()
    with pytest.raises(kinds.InvalidEvent):
        emitter.emit(kinds.OPERATOR_FAILED, {"operator": "x"})
    with pytest.raises(kinds.UnknownEventKind):
        emitter.emit("made_up_kind", {})
    assert memory.events == [] and emitter.last_seq == 0  # a refused event takes no number


def test_registering_a_kind_twice_is_an_error_and_a_new_kind_works():
    with pytest.raises(ValueError):
        kinds.register_kind(kinds.NODE_VISITED)
    name = kinds.register_kind("test_only_kind_a", ("x",))
    emitter, memory = _emitter()
    emitter.emit(name, {"x": 1})
    assert memory.events[0].kind == name


def test_an_oversized_payload_goes_out_as_a_stub_and_is_kept_whole():
    emitter, _ = _emitter()
    big = {"operator": "where", "inputs": {"rows": ["x" * 50] * 200}}
    event = emitter.emit(kinds.OPERATOR_STARTED, big, parent=3)
    assert len(json.dumps(event.payload)) < DEFAULT_PAYLOAD_CAP_BYTES
    stub = event.payload["overflow"]
    assert stub["keys"] == ["inputs", "operator"] and stub["bytes"] > DEFAULT_PAYLOAD_CAP_BYTES
    assert emitter.overflow[stub["ref"]] == big
    small = emitter.emit(kinds.NODE_VISITED, {"node": "a"})
    assert "overflow" not in small.payload and event.parent_id == 3


def test_a_failing_sink_is_recorded_and_does_not_stop_the_run():
    class Broken:
        def emit(self, event: Event) -> None:
            raise RuntimeError("down")

    emitter, memory = _emitter(Broken())
    emitter.emit(kinds.NODE_VISITED, {"node": "a"})
    emitter.emit(kinds.NODE_VISITED, {"node": "b"})
    assert [e.seq for e in memory.events] == [1, 2]
    assert [seq for seq, _ in emitter.sink_failures] == [1, 2]


def test_an_event_round_trips_to_identical_text():
    emitter, memory = _emitter()
    emitter.emit(kinds.EXCLUSION, {"metric": "m", "reason_code": "twin", "check_value": 0.98}, depth=2, branch=1, parent=1)
    [event] = memory.events
    again = Event.from_json(event.to_json())
    assert again == event and again.to_json() == event.to_json()


def test_a_loaded_graph_becomes_events_with_every_exclusion_visible():
    document = json.loads(VENDORED_GRAPH.read_text(encoding="utf-8"))
    fixture = VENDORED_GRAPH.parents[4] / "eval" / "diag" / "fixtures" / "brand20_daily_2026-10.json"
    facts = facts_from_definitions(json.loads(fixture.read_text(encoding="utf-8"))["definitions"])
    loaded = load_graph(document, live=facts, catalogue_version="1635beb77932")
    emitter, memory = _emitter()
    emit_graph_loaded(emitter, loaded)
    head, *rest = memory.events
    assert head.kind == kinds.GRAPH_LOADED and head.payload["graph_version"] == "3116740b8d70"
    assert head.payload["issues"] == len(loaded.issues) == len(rest)
    assert all(e.parent_id == head.seq for e in rest)
    excluded = {e.payload["metric"] for e in rest if e.kind == kinds.EXCLUSION}
    assert excluded == set(loaded.excluded_nodes) and len(excluded) == 152
    assert {e.payload["code"] for e in rest if e.kind == kinds.GRAPH_ISSUE} == {"node_status_broken"}


def test_events_reach_the_mission_stream_under_one_key_and_survive_the_privacy_filter():
    seen: list[tuple[str, str, dict]] = []
    set_progress_sink("m-1", lambda kind, summary, payload: seen.append((kind, summary, payload)))
    try:
        emitter = Emitter("inv-1", _clock(), [MissionEventSink("m-1")])
        emitter.emit(kinds.OPERATOR_STARTED, {"operator": "when", "inputs": {"metric": "m"}})
    finally:
        clear_progress_sink("m-1")
    [(kind, summary, payload)] = seen
    assert kind == "diag.operator_started" and summary == "operator started: when"
    assert set(payload) == {"investigation"} and payload["investigation"]["payload"]["operator"] == "when"

    principal = Principal(
        principal_id="u", workspace_id="w", user_id="u", authenticated=True, auth_method=PrincipalAuthMethod.SHARED_API_KEY
    )
    activity = ActivityEvent(
        thread_id="t", workspace_id="w", owner_user_id="u", event_type=kind,
        payload={**payload, "scratchpad": "never delivered", "mission_id": "m-1"},
    )
    delivered = event_for_principal(activity, principal)
    assert delivered is not None
    assert delivered.payload["investigation"]["seq"] == 1
    assert "scratchpad" not in delivered.payload and delivered.payload["mission_id"] == "m-1"

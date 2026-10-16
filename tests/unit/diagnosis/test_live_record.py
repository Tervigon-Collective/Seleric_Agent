"""A live diagnosis is recorded and replayable (plan P2.6, D-46): the tool gathers through the series port, runs the
engine as the replayable runner, and keeps the record. The semantic layer is the eval world; the tool is the real one."""

from __future__ import annotations

import ast
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from seleric_swarm.agent.progress import clear_progress_sink, set_progress_sink
from seleric_swarm.diagnosis.adapters import (
    legacy_engine_runner,  # noqa: F401  (registers the runner it replays)
)
from seleric_swarm.diagnosis.adapters.legacy_trace import LegacyTrace, TraceForward
from seleric_swarm.diagnosis.adapters.live_record import save_record
from seleric_swarm.diagnosis.adapters.runstore_artifacts import ARTIFACT_TYPE
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.gather import GatherPlan
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.record import (
    RunRecord,
    decode_series,
    encode_series,
    request_from_dict,
)
from seleric_swarm.diagnosis.domain.types import (
    Scope,
    SeriesRequest,
    SeriesResult,
    Window,
    result_fingerprint,
)
from seleric_swarm.diagnosis.operators.gather import gather
from seleric_swarm.diagnosis.ports.runstore import RunStore
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.run import replay
from seleric_swarm.toolsets import diagnosis

START, END = datetime(2026, 9, 27, tzinfo=UTC), datetime(2026, 9, 29, tzinfo=UTC)
TOOL = Path(diagnosis.__file__)


def _fresh(ctx):
    diagnosis._definitions_cache.update({"at": 0.0, "ids": frozenset(), "defs": {}})
    return ctx


async def _run(cube_world):
    ctx, _ = cube_world
    seen: list[tuple[str, dict]] = []
    set_progress_sink("t", lambda kind, summary, payload: seen.append((kind, payload)))
    try:
        result = await diagnosis.diagnose_metric_change(_fresh(ctx), "net_sales", event_start=START, event_end=END)
    finally:
        clear_progress_sink("t")
    return ctx, result, [p["investigation"] for k, p in seen if k.startswith("diag.")]


def _record(ctx) -> RunRecord:
    [artifact] = [a for a in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id) if a.artifact_type == ARTIFACT_TYPE]
    return RunRecord.from_dict(artifact.payload)


async def test_the_tool_keeps_a_record_that_replays_byte_for_byte(cube_world):
    ctx, result, live = await _run(cube_world)
    assert result.success
    record = _record(ctx)
    # what the engine reported while it ran is on the live trace exactly once, no more and no less than in the record
    for kind in (kinds.HYPOTHESIS_RESOLVED, kinds.REFUTER_RESULT, kinds.UNEXPLAINED_SHARE_UPDATED, kinds.OPERATOR_FAILED):
        assert [e["payload"] for e in live if e["kind"] == kind] == [e["payload"] for e in record.events if e["kind"] == kind], kind
    assert sum(e["kind"] == kinds.HYPOTHESIS_RESOLVED for e in live) > 0
    assert record.investigation_id == result.provenance.source_metadata["investigation_id"]
    assert record.runner == "legacy_engine" and "plan" in record.request and "engine_input" not in record.request
    assert record.snapshots and record.estimates and record.runtime_trace["clock"]
    replayed = await replay(RunRecord.from_json(record.to_json()))  # through text, as a store keeps it
    assert replayed.identical, replayed.divergence
    # the report the record holds is the one the tool answered with
    answered = result.provenance.source_metadata["diagnosis"]
    assert record.report is not None and record.report["verdict"] == answered["verdict"]
    assert [d["driver"] for d in record.report["drivers"]] == [d["driver"] for d in answered["drivers"]]


async def test_a_budget_that_cuts_the_drivers_is_part_of_the_record(cube_world, monkeypatch):
    ctx, _ = cube_world
    real = ctx.deps.budget.consume
    calls: list[int] = []

    def tight(name: str, *a: Any, **k: Any):
        if name == "causal_queries":
            calls.append(1)
            if len(calls) > 2:
                return type("Refused", (), {"ok": False})()
        return real(name, *a, **k)

    monkeypatch.setattr(ctx.deps.budget, "consume", tight)
    _, result, _ = await _run(cube_world)
    assert result.success and len(calls) > 2
    record = _record(ctx)
    assert record.request["plan"]["estimate_cap"] == 2  # the budget cut how many confirmed candidates are estimated
    replayed = await replay(RunRecord.from_json(record.to_json()))
    assert replayed.identical, replayed.divergence
    estimated = [d for d in result.provenance.source_metadata["diagnosis"]["drivers"] if d["status"] != "excluded"]
    assert len(estimated) <= 2


async def test_the_run_is_on_the_live_trace_once_and_in_the_order_the_engine_ran_it(cube_world):
    _, _, events = await _run(cube_world)
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(seqs) + 1))
    names = [e["kind"] for e in events]
    for once in (kinds.INVESTIGATION_STARTED, kinds.WINDOW_RESOLVED, kinds.INVESTIGATION_FINISHED):
        assert names.count(once) == 1, once
    started = [e["payload"]["operator"] for e in events if e["kind"] == kinds.OPERATOR_STARTED]
    assert [o for o in started if o in {"is_real", "what", "where", "why_event"}] == ["is_real", "what", "where", "why_event"]
    by_seq = {e["seq"]: e for e in events}
    [is_real] = [e for e in events if e["kind"] == kinds.OPERATOR_STARTED and e["payload"]["operator"] == "is_real"]
    assert by_seq[is_real["parent_id"]]["payload"]["operator"] == "analyse"  # the stages still sit inside the 'analyse' row
    assert all(e["parent_id"] in by_seq for e in events if e["parent_id"] is not None)


async def test_a_record_whose_series_were_edited_does_not_replay(cube_world):
    ctx, _, _ = await _run(cube_world)
    record = _record(ctx)
    raw = record.to_dict()
    snap = next(s for s in raw["snapshots"] if request_from_dict(s["request"]).metric_id == "net_sales" and not s["request"]["dimension"])
    values, segments = decode_series(raw["blobs"][snap["data_ref"]])
    day = max(values)
    values[day] *= 1.5
    request = request_from_dict(snap["request"])
    raw["blobs"][snap["data_ref"]] = encode_series(SeriesResult(request, values, segments, snap["source_query"], snap["fingerprint"]))
    with pytest.raises(Exception, match="fingerprint|edited"):
        await replay(RunRecord.from_dict(raw))


async def test_a_store_that_fails_does_not_fail_the_answer_and_says_so(cube_world, monkeypatch, caplog):
    class Broken:
        def save(self, record):
            raise RuntimeError("store down")

        def load(self, investigation_id):
            raise AssertionError

    monkeypatch.setattr(diagnosis, "ArtifactRunStore", lambda *a, **k: Broken())
    with caplog.at_level("ERROR"):
        _, result, events = await _run(cube_world)
    assert result.success
    failed = [e for e in events if e["kind"] == kinds.OPERATOR_FAILED and e["payload"]["operator"] == "save_run_record"]
    assert len(failed) == 1 and failed[0]["payload"]["error_type"] == "RuntimeError"
    assert any("run_record_not_saved" in r.getMessage() for r in caplog.records)


def test_save_record_reports_success_and_failure():
    class Memory:
        def __init__(self):
            self.kept = []

        def save(self, record):
            self.kept.append(record)

    class Failing:
        def save(self, record):
            raise OSError("disk")

    stores: list[RunStore] = [Memory(), Failing()]  # type: ignore[list-item]
    assert save_record(stores[0], object()) is True  # type: ignore[arg-type]
    assert stores[0].kept  # type: ignore[attr-defined]
    assert save_record(stores[1], type("R", (), {"investigation_id": "inv_x"})()) is False  # type: ignore[arg-type]


def test_the_tool_reaches_the_data_only_through_the_series_port():
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {"_fetch_all", "_fetch_daily", "_prefetch_daily"}
    }
    attrs = {
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {"_fetch_all", "_fetch_daily", "_prefetch_daily"}
    }
    assert called == set() and attrs == set()


# ----------------------------------------------------------------------------- the gather step on a stub port
DAYS = [date(2026, 9, 1) + timedelta(days=i) for i in range(30)]
EVENT = (date(2026, 9, 29), date(2026, 9, 30))


class StubPort:
    """Answers each request from a table; ``dropped`` metrics come back with their scope dropped, ``broken`` ones fail."""

    def __init__(self, table: dict[str, dict[date, float]], *, dropped: set[str] = frozenset(), broken: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.table, self.dropped, self.broken = table, dropped, broken
        self.requests: list[SeriesRequest] = []

    async def fetch(self, requests):
        out = []
        for r in requests:
            self.requests.append(r)
            values = {} if r.dimension else dict(self.table.get(r.metric_id, {}))
            segments = {"a": dict(self.table.get(r.metric_id, {})), "b": dict(self.table.get(r.metric_id, {}))} if r.dimension else {}
            error = "boom" if r.metric_id in self.broken else None
            out.append(SeriesResult(
                r, {} if error else values, {} if error else segments, json.dumps({"measures": [r.metric_id]}),
                result_fingerprint(r, values, segments), error, "USD", ("channel",) if r.metric_id in self.dropped else (), len(values) or len(segments),
            ))
        return out


def _plan(**over: Any) -> GatherPlan:
    lineage = {m: MetricMeta(m, "additive", "v") for m in ("sales", "orders", "spend", "total_a", "total_b")}
    base: dict[str, Any] = {
        "outcome": "sales", "start": DAYS[0], "end": EVENT[1], "scope": {"channel": "meta"},
        "event_days": tuple(date(2026, 9, 29) + timedelta(days=i) for i in range(2)), "partial_days": frozenset(),
        "baseline_days": (), "claimed_direction": None, "lineage": lineage,
        "series_ids": ("sales", "orders", "spend", "total_a", "total_b"), "core": frozenset({"sales", "orders"}),
        "change_log": frozenset(), "ladder_ends": frozenset(), "bridge_pool": ("total_a", "total_b"),
        "drivers": ("orders", "spend"), "dims": ("platform",), "chain_dims": (), "scope_tokens": frozenset({"brand"}),
    }
    base.update(over)
    return GatherPlan(**base)


def _table(*metrics: str) -> dict[str, dict[date, float]]:
    return {m: {d: 100.0 + i + (7 * (j + 1)) for i, d in enumerate(DAYS + list(EVENT))} for j, m in enumerate(metrics)}


def test_a_plan_survives_its_text_form():
    plan = _plan()
    assert GatherPlan.from_dict(json.loads(json.dumps(plan.to_dict()))) == plan


async def test_gather_builds_the_input_the_plan_describes():
    table = _table("sales", "orders", "spend", "total_a", "total_b")
    port = StubPort(table)
    got = await gather(_plan(), port, cfg=DiagnosisConfig())
    assert got.failure is None and got.input is not None
    assert set(got.input.series) == set(table) and got.input.candidate_drivers == ["orders", "spend"]
    assert got.input.bridge_candidates == ["total_a", "total_b"] and got.units["sales"] == "USD"
    assert set(got.input.segments["sales"]) == {"platform"} and ("sales", "platform") in got.segment_args
    assert all(r.scope == Scope.from_filters({"channel": "meta"}) and r.window == Window(DAYS[0], EVENT[1]) for r in port.requests)


async def test_a_series_that_cannot_take_the_scope_is_left_out_unless_it_is_the_outcome():
    table = _table("sales", "orders", "spend", "total_a", "total_b")
    port = StubPort(table, dropped={"sales", "spend", "total_a", "total_b"})
    got = await gather(_plan(), port, cfg=DiagnosisConfig())
    assert got.input is not None
    assert "sales cannot be filtered by channel; used unfiltered" in got.notes
    assert "spend is not recorded by channel, so it is left out of this scope" in got.notes
    assert "spend" not in got.input.series and "orders" in got.input.series
    # a bridge term that is not otherwise needed leaves the pool silently: an unfiltered total cannot sum to a filtered outcome
    assert got.input.bridge_candidates == [] and not any("total_a" in n for n in got.notes)


async def test_a_logged_edit_count_for_the_whole_account_is_not_this_scope():
    plan = _plan(change_log=frozenset({"orders"}), series_ids=("sales", "orders"), bridge_pool=(), drivers=())
    got = await gather(plan, StubPort(_table("sales", "orders"), dropped={"orders"}), cfg=DiagnosisConfig())
    assert got.input is not None and "orders" not in got.input.series
    assert got.notes == ["orders is not recorded by channel, so it is left out of this scope"]


async def test_an_outcome_that_cannot_be_read_is_a_failure_not_an_empty_answer():
    plan = _plan(series_ids=("sales", "orders"), bridge_pool=(), drivers=())
    failed = await gather(plan, StubPort(_table("sales", "orders"), broken={"sales"}), cfg=DiagnosisConfig())
    assert failed.input is None and failed.failure == "fetch_failed" and failed.failure_detail == "boom"
    empty = await gather(plan, StubPort(_table("orders")), cfg=DiagnosisConfig())
    assert empty.input is None and empty.failure == "no_data"
    other = await gather(plan, StubPort(_table("sales", "orders"), broken={"orders"}), cfg=DiagnosisConfig())
    assert other.input is not None and other.notes == ["orders: fetch failed (boom)"]


async def test_a_split_of_too_many_rows_is_not_a_split():
    table = _table("sales")
    port = StubPort(table)
    real = port.fetch

    async def many(requests):
        return [SeriesResult(r.request, r.values, r.segments, r.source_query, r.fingerprint, r.error, r.currency, r.dropped_filters, 99999)
                if r.request.dimension else r for r in await real(requests)]

    port.fetch = many  # type: ignore[method-assign]
    got = await gather(_plan(series_ids=("sales",), bridge_pool=(), drivers=()), port, cfg=DiagnosisConfig())
    assert got.input is not None and got.input.segments == {}


def test_forwarded_events_land_under_the_live_trace_with_their_parents_mapped():
    from seleric_swarm.diagnosis.domain.events import Event

    trace = LegacyTrace("", "net_sales", {}, sinks=())
    forward = TraceForward(trace)

    def event(seq, kind, payload, parent=None):
        return Event(trace.investigation_id, seq, datetime(2026, 1, 1, tzinfo=UTC), 0, 0, parent, kind, 1, payload)

    forward.emit(event(1, kinds.INVESTIGATION_STARTED, {"outcome": "x", "scope": {}}))
    trace.started()
    with trace.operator("analyse"):
        forward.emit(event(2, kinds.OPERATOR_STARTED, {"operator": "is_real", "inputs": {}}))
        forward.emit(event(3, kinds.OPERATOR_FINISHED, {"operator": "is_real", "query_count": 0, "duration_ms": 1, "result_digest": ""}, 2))
    mine = [e.to_dict() for e in trace.memory.events]
    assert [e["kind"] for e in mine] == [kinds.INVESTIGATION_STARTED, kinds.OPERATOR_STARTED, kinds.OPERATOR_STARTED, kinds.OPERATOR_FINISHED, kinds.OPERATOR_FINISHED]
    analyse, is_real, closed = mine[1], mine[2], mine[3]
    assert is_real["parent_id"] == analyse["seq"] and closed["parent_id"] == is_real["seq"]


# ----------------------------------------------------------------------------- retention (D-49)
def _record_artifact(store, record_id: str, *, thread: str, age_days: int, kind: str = ARTIFACT_TYPE):
    from datetime import timedelta

    from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance

    store.put(Artifact(
        id=record_id, workspace_id="w", artifact_type=kind, payload={}, classification="derived", evidence_ids=["e"],
        provenance=ArtifactProvenance(evidence_ids=["e"], calculation_version="t"), mission_id="m", thread_id=thread,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    ))


def test_records_leave_with_their_thread_and_only_records():
    from seleric_swarm.diagnosis.adapters.runstore_artifacts import purge_thread
    from seleric_swarm.state.artifacts import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    _record_artifact(store, "a", thread="t1", age_days=1)
    _record_artifact(store, "b", thread="t2", age_days=1)
    _record_artifact(store, "c", thread="t1", age_days=1, kind="evidence")
    assert purge_thread(store, "t1") == 1
    assert store.get("a") is None and store.get("b") is not None and store.get("c") is not None


def test_records_expire_after_ninety_days_and_a_save_sweeps_them():
    from seleric_swarm.diagnosis.adapters.runstore_artifacts import (
        RETENTION_DAYS,
        purge_expired,
    )
    from seleric_swarm.state.artifacts import InMemoryArtifactStore

    assert RETENTION_DAYS == 90
    store = InMemoryArtifactStore()
    _record_artifact(store, "old", thread="t", age_days=91)
    _record_artifact(store, "fresh", thread="t", age_days=89)
    _record_artifact(store, "old_evidence", thread="t", age_days=400, kind="evidence")
    assert purge_expired(store) == 1
    assert store.get("old") is None and store.get("fresh") is not None and store.get("old_evidence") is not None


async def test_a_diagnosis_sweeps_expired_records_and_keeps_its_own(cube_world):
    ctx, _ = cube_world
    _record_artifact(ctx.deps.artifact_store, "expired", thread="t", age_days=100)
    _, result, _ = await _run(cube_world)
    assert result.success
    assert ctx.deps.artifact_store.get("expired") is None
    assert len([a for a in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id) if a.artifact_type == ARTIFACT_TYPE]) == 1


def test_a_delete_needs_a_filter():
    from seleric_swarm.state.artifacts import InMemoryArtifactStore

    with pytest.raises(ValueError, match="thread or a cut-off"):
        InMemoryArtifactStore().delete_where(ARTIFACT_TYPE)

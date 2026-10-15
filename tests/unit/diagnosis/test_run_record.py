"""Run record and replay (plan P1.7, D-21): a recorded investigation repeats byte for byte from the record alone."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import date
from typing import Any, ClassVar

import pytest

from seleric_swarm.diagnosis.adapters.runstore_artifacts import ArtifactRunStore
from seleric_swarm.diagnosis.adapters.runstore_file import FileRunStore
from seleric_swarm.diagnosis.adapters.runstore_memory import MemoryRunStore
from seleric_swarm.diagnosis.adapters.runtime import SeededRuntime
from seleric_swarm.diagnosis.cli.replay import main as replay_cli
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.record import RunRecord, decode_series, encode_series
from seleric_swarm.diagnosis.domain.types import SeriesRequest, SeriesResult, Window
from seleric_swarm.diagnosis.ports.runstore import RunRecordNotFound
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.run import (
    RUNNERS,
    ReplayDivergence,
    RunInputs,
    record_run,
    replay,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore

WINDOW = Window(date(2026, 9, 1), date(2026, 9, 20))
REQUEST = {"outcome": "net_sales", "metric": "ad_spend"}


class Probe:
    """A small deterministic 'investigation': reads a series, draws a bootstrap interval from its seeded stream,
    checks for cancellation, times itself with the runtime clock, and emits a few events including a big one."""

    kind: ClassVar[str] = "probe"
    cache_hit: ClassVar[bool] = False

    async def run(self, inputs: RunInputs) -> dict[str, Any]:
        e = inputs.emitter
        e.emit(kinds.INVESTIGATION_STARTED, {"outcome": inputs.request["outcome"], "scope": {}})
        started = inputs.runtime.now()
        if inputs.runtime.cancelled():
            e.emit(kinds.INVESTIGATION_CANCELLED, {"reason": "requested"})
            return {"cancelled": True}
        [result] = await inputs.series.fetch([SeriesRequest(inputs.request["metric"], WINDOW)])
        e.emit(kinds.QUERY_ISSUED, {"metric": inputs.request["metric"], "cache_hit": self.cache_hit})
        values = [v for _, v in sorted(result.values.items())]
        draws = inputs.runtime.rng("probe.bootstrap").choice(values, size=(200, len(values)))
        means = sorted(float(row.mean()) for row in draws)
        elapsed = (inputs.runtime.now() - started).total_seconds()
        e.emit(kinds.OPERATOR_FINISHED, {"operator": "probe", "query_count": 1, "duration_ms": round(elapsed * 1000), "result_digest": result.fingerprint})
        e.emit(kinds.OPERATOR_STARTED, {"operator": "big", "inputs": {"values": values * 40}})
        return {"mean": sum(values) / len(values), "interval": [means[5], means[194]], "fingerprint": result.fingerprint}


@pytest.fixture(autouse=True)
def _probe_registered():
    if "probe" not in RUNNERS.kinds():
        RUNNERS.register(Probe)
    yield


async def _record(file_adapter, **kw) -> RunRecord:
    return await record_run(
        Probe(), investigation_id="inv-probe", request=REQUEST, config=DiagnosisConfig(), seed=7, series=file_adapter,
        runtime=SeededRuntime(7), **kw,
    )


async def test_a_recorded_run_replays_byte_identical_from_the_record_alone(file_adapter):
    record = await _record(file_adapter)
    assert record.report and record.report["mean"] > 0 and len(record.events) >= 4
    assert any("overflow" in e["payload"] for e in record.events) and record.overflow  # the big payload is kept whole
    again = RunRecord.from_json(record.to_json())  # through text, as a stored record would be
    result = await replay(again)
    assert result.identical, result.divergence
    assert result.outcome and [json.dumps(e, sort_keys=True) for e in result.outcome.events] == [json.dumps(dict(e), sort_keys=True) for e in record.events]
    assert record.versions["libs"]["dowhy"] and record.versions["engine"]


async def test_the_record_holds_the_data_the_run_read(file_adapter):
    record = await _record(file_adapter)
    [snapshot] = record.snapshots
    values, segments = decode_series(record.blobs[snapshot.data_ref])
    [direct] = await file_adapter.fetch([SeriesRequest("ad_spend", WINDOW)])
    assert values == dict(direct.values) and segments == {} and snapshot.fingerprint == direct.fingerprint


async def test_a_record_whose_data_was_edited_is_refused(file_adapter):
    raw = (await _record(file_adapter)).to_dict()
    ref = raw["snapshots"][0]["data_ref"]
    values, segments = decode_series(raw["blobs"][ref])
    first = next(iter(values))
    values[first] += 1.0
    raw["blobs"][ref] = encode_series(SeriesResult(SeriesRequest("ad_spend", WINDOW), values, segments, "", ""))
    with pytest.raises(ReplayDivergence):
        await replay(RunRecord.from_dict(raw))


async def test_a_change_in_the_engine_shows_up_as_the_first_differing_event(file_adapter):
    record = await _record(file_adapter)

    class Changed(Probe):
        async def run(self, inputs: RunInputs) -> dict[str, Any]:
            report = await super().run(inputs)
            return {**report, "mean": report["mean"] + 1}

    result = await replay(record, lambda _kind: Changed())
    assert not result.identical and "report differs" in (result.divergence or "")

    class Different(Probe):
        cache_hit: ClassVar[bool] = True  # same number of events and clock readings, one payload differs

    result = await replay(record, lambda _kind: Different())
    assert not result.identical and (result.divergence or "").startswith("event 2 differs")

    class Chattier(Probe):
        async def run(self, inputs: RunInputs) -> dict[str, Any]:
            inputs.emitter.emit(kinds.NODE_VISITED, {"node": "extra"})  # an extra event also reads the clock once more
            return await super().run(inputs)

    result = await replay(record, lambda _kind: Chattier())
    assert not result.identical and "time more often" in (result.divergence or "")


async def test_a_replay_that_asks_for_something_the_run_never_had_is_a_divergence_not_a_guess(file_adapter):
    record = await _record(file_adapter)

    class Greedy(Probe):
        async def run(self, inputs: RunInputs) -> dict[str, Any]:
            await inputs.series.fetch([SeriesRequest("orders", WINDOW)])
            return await super().run(inputs)

    result = await replay(record, lambda _kind: Greedy())
    assert not result.identical and "never made" in (result.divergence or "")

    class Impatient(Probe):
        async def run(self, inputs: RunInputs) -> dict[str, Any]:
            for _ in range(50):
                inputs.runtime.now()
            return await super().run(inputs)

    result = await replay(record, lambda _kind: Impatient())
    assert not result.identical and "time more often" in (result.divergence or "")


async def test_a_run_that_was_cancelled_replays_as_cancelled(file_adapter):
    flag = {"stop": True}
    record = await record_run(
        Probe(), investigation_id="inv-stop", request=REQUEST, config=DiagnosisConfig(), seed=1, series=file_adapter,
        runtime=SeededRuntime(1, is_cancelled=lambda: flag["stop"]),
    )
    assert record.report == {"cancelled": True} and record.snapshots == ()
    assert (await replay(record)).identical


def test_the_replay_command_reports_identical_diverged_and_unreadable(file_adapter, tmp_path, capsys):
    record = asyncio.run(_record(file_adapter))
    good = tmp_path / "good.json"
    good.write_text(record.to_json(), encoding="utf-8")
    assert replay_cli([str(good)]) == 0 and "identical" in capsys.readouterr().out
    bad = copy.deepcopy(record.to_dict())
    bad["report"]["mean"] += 1
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    assert replay_cli([str(tmp_path / "bad.json")]) == 1 and "DIVERGED" in capsys.readouterr().err
    assert replay_cli([str(tmp_path / "missing.json")]) == 2
    unknown = copy.deepcopy(record.to_dict())
    unknown["runner"] = "no-such-runner"
    (tmp_path / "unknown.json").write_text(json.dumps(unknown), encoding="utf-8")
    assert replay_cli([str(tmp_path / "unknown.json")]) == 2


@pytest.fixture(params=["memory", "file", "artifacts"])
def run_store(request, tmp_path):
    if request.param == "memory":
        return MemoryRunStore()
    if request.param == "file":
        return FileRunStore(tmp_path / "records")
    return ArtifactRunStore(InMemoryArtifactStore(), workspace_id="w1", mission_id="m1", thread_id="t1")


async def test_run_store_contract(run_store, file_adapter):
    record = await _record(file_adapter)
    run_store.save(record)
    loaded = run_store.load(record.investigation_id)
    assert loaded.to_json() == record.to_json()
    assert (await replay(loaded)).identical
    with pytest.raises(Exception):  # noqa: B017  (each store refuses a second write in its own error type)
        run_store.save(record)
    with pytest.raises(RunRecordNotFound):
        run_store.load("never-saved")


async def test_the_artifact_store_scopes_the_record_to_its_mission_and_thread(file_adapter):
    store = InMemoryArtifactStore()
    record = await _record(file_adapter)
    ArtifactRunStore(store, workspace_id="w1", mission_id="m1", thread_id="t1", run_id="r1").save(record)
    [artifact] = store.list_for_mission("m1")
    assert artifact.artifact_type == "diagnosis_run_record" and artifact.thread_id == "t1" and artifact.classification == "derived"
    assert artifact.evidence_ids and all(i.startswith("series_snapshot:") for i in artifact.evidence_ids)


class SplitProbe:
    """Asks for a split series twice over: a window with data, then one before any data exists."""

    kind: ClassVar[str] = "split-probe"

    async def run(self, inputs: RunInputs) -> dict[str, Any]:
        early = Window(date(2020, 1, 1), date(2020, 1, 10))
        full, empty = await inputs.series.fetch([
            SeriesRequest("ad_spend", WINDOW, dimension="ad_platform"),
            SeriesRequest("ad_spend", early, dimension="ad_platform"),
        ])
        inputs.emitter.emit(kinds.QUERY_ISSUED, {"metric": "ad_spend", "cache_hit": False})
        return {"segments": sorted(full.segments), "empty": sorted(empty.segments)}


async def test_a_split_request_that_came_back_empty_replays_and_is_not_called_corrupt(file_adapter):
    record = await record_run(
        SplitProbe(), investigation_id="inv-empty", request={}, config=DiagnosisConfig(), seed=1, series=file_adapter,
        runtime=SeededRuntime(1),
    )
    assert record.report is not None and record.report["empty"] == [] and record.report["segments"]
    assert (await replay(record, lambda _kind: SplitProbe())).identical


async def test_fetching_a_recorded_request_more_often_than_the_run_did_is_a_divergence(file_adapter):
    record = await _record(file_adapter)

    class Twice(Probe):
        async def run(self, inputs: RunInputs) -> dict[str, Any]:
            await inputs.series.fetch([SeriesRequest(inputs.request["metric"], WINDOW)])
            return await super().run(inputs)

    result = await replay(record, lambda _kind: Twice())
    assert not result.identical and "more often" in (result.divergence or "")


def test_the_eval_scoring_guard_watches_the_new_package_too():
    import runner

    assert "src/seleric_swarm/diagnosis" in runner.ENGINE_PATHS

"""Run an investigation and record it, or replay a record (plan P1.7).

``execute`` is the single path both take, so a replay cannot differ from the recorded run except through its inputs:
the runner gets a series port, a runtime and an emitter, and nothing else non-deterministic. Recording wraps the real
ports and writes down what crossed them; replay serves exactly that back."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Protocol

import numpy

from seleric_swarm.diagnosis.domain.canonical import canonical_json
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.errors import DiagnosisError
from seleric_swarm.diagnosis.domain.estimates import (
    TimeSeriesEffect,
    effect_from_json,
    effect_to_json,
    job_key,
)
from seleric_swarm.diagnosis.domain.graph import Graph
from seleric_swarm.diagnosis.domain.record import (
    EstimateSnapshot,
    RunRecord,
    SeriesSnapshot,
    decode_series,
    decode_series_with_meta,
    encode_series,
    request_from_dict,
    request_key,
    request_to_dict,
)
from seleric_swarm.diagnosis.domain.registry import Registry
from seleric_swarm.diagnosis.domain.types import SeriesRequest, SeriesResult, result_fingerprint
from seleric_swarm.diagnosis.libs.versions import library_versions
from seleric_swarm.diagnosis.ports.estimator import EffectEstimator
from seleric_swarm.diagnosis.ports.events import EventSink
from seleric_swarm.diagnosis.ports.runtime import Runtime
from seleric_swarm.diagnosis.ports.series import SeriesPort
from seleric_swarm.diagnosis.trace.correlation import investigation_scope
from seleric_swarm.diagnosis.trace.emitter import Emitter, MemorySink
from seleric_swarm.diagnosis.trace.streams import stream_rng

# What the engine decides, changed whenever the same input can give a different answer or report (not for a refactor).
# 0: the legacy engine split into operators (Phase 2). 1: the reviewed graph chooses the candidate causes, a driver whose
# path stayed quiet is ruled out, no cause is accepted on an ordinary day, the report gains `measurement` (P3.2).
# A record replays only on the engine that made it; another engine's record is refused, not reported as a divergence.
ENGINE_VERSION = "diagnosis-core/1"


class ReplayDivergence(DiagnosisError):
    code = "replay_divergence"


class RecordFromAnotherEngine(DiagnosisError):
    code = "record_from_another_engine"


@dataclass(frozen=True)
class RunInputs:
    """Everything a runner may use."""

    investigation_id: str
    request: Mapping[str, Any]
    config: DiagnosisConfig
    seed: int
    series: SeriesPort
    runtime: Runtime
    emitter: Emitter
    graph: Graph | None = None
    estimator: EffectEstimator | None = None


class Runner(Protocol):
    kind: ClassVar[str]

    async def run(self, inputs: RunInputs) -> dict[str, Any]:
        """Run the investigation and return its report (JSON-able)."""
        ...


RUNNERS: Registry[Runner] = Registry("runner")
register_runner = RUNNERS.register


class RecordingSeriesPort:
    """Passes requests to the real port and keeps every answer, in order."""

    def __init__(self, inner: SeriesPort) -> None:
        self._inner = inner
        self.snapshots: list[SeriesSnapshot] = []
        self.blobs: dict[str, str] = {}

    async def fetch(self, requests: Sequence[SeriesRequest]) -> list[SeriesResult]:
        results = await self._inner.fetch(requests)
        for request, result in zip(requests, results, strict=True):
            blob = encode_series(result)
            ref = f"blob-{len(self.snapshots) + 1}"
            self.blobs[ref] = blob
            self.snapshots.append(
                SeriesSnapshot(request_key(request), request_to_dict(request), result.source_query, result.fingerprint, ref, result.error)
            )
        return results


class RecordingEstimator:
    """Passes jobs to the real estimator and keeps every answer, in order. The estimates are inputs of a replay like
    the series are: their last digits depend on the numerical stack and the process they ran in, and a replay must
    test the engine's logic, not the machine's arithmetic."""

    def __init__(self, inner: EffectEstimator) -> None:
        self._inner = inner
        self.snapshots: list[EstimateSnapshot] = []

    def estimate_many(self, jobs: Sequence[Mapping[str, Any]]) -> list[TimeSeriesEffect]:
        results = self._inner.estimate_many(jobs)
        for job, result in zip(jobs, results, strict=True):
            self.snapshots.append(EstimateSnapshot(job_key(job), effect_to_json(result)))
        return results


class ReplayEstimator:
    """Serves the recorded estimates in the order they were given; a job the recorded run never submitted (or submitted
    fewer times) is a divergence: the engine asked a different question than it did."""

    def __init__(self, record: RunRecord) -> None:
        self._queues: dict[str, list[Mapping[str, Any]]] = {}
        for snap in record.estimates:
            self._queues.setdefault(snap.key, []).append(snap.result)
        self._served: dict[str, int] = {}

    def estimate_many(self, jobs: Sequence[Mapping[str, Any]]) -> list[TimeSeriesEffect]:
        out: list[TimeSeriesEffect] = []
        for job in jobs:
            key = job_key(job)
            queue = self._queues.get(key)
            index = self._served.get(key, 0)
            if not queue or index >= len(queue):
                raise ReplayDivergence(
                    f"the replay asked for an estimate ({job.get('treatment_name')} -> {job.get('outcome_name')}) "
                    "the recorded run did not ask for"
                )
            self._served[key] = index + 1
            out.append(effect_from_json(queue[index]))
        return out


class RecordingRuntime:
    """Passes through to the real runtime and writes down each clock reading and cancel check."""

    def __init__(self, inner: Runtime) -> None:
        self._inner = inner
        self.clock: list[str] = []
        self.cancel: list[bool] = []

    def now(self) -> datetime:
        value = self._inner.now()
        self.clock.append(value.isoformat())
        return value

    def rng(self, stream: str) -> numpy.random.Generator:
        return self._inner.rng(stream)

    def cancelled(self) -> bool:
        value = self._inner.cancelled()
        self.cancel.append(value)
        return value


class ReplayRuntime:
    """Returns the recorded clock readings and cancel answers in order; random streams come from the recorded seed."""

    def __init__(self, seed: int, trace: Mapping[str, Any]) -> None:
        self._seed = seed
        self._clock = [datetime.fromisoformat(v) for v in trace.get("clock", [])]
        self._cancel = [bool(v) for v in trace.get("cancel", [])]
        self._i_clock = 0
        self._i_cancel = 0

    def now(self) -> datetime:
        if self._i_clock >= len(self._clock):
            raise ReplayDivergence("the replay asked for the time more often than the recorded run did")
        self._i_clock += 1
        return self._clock[self._i_clock - 1]

    def rng(self, stream: str) -> numpy.random.Generator:
        return stream_rng(self._seed, stream)

    def cancelled(self) -> bool:
        if self._i_cancel >= len(self._cancel):
            raise ReplayDivergence("the replay checked for cancellation more often than the recorded run did")
        self._i_cancel += 1
        return self._cancel[self._i_cancel - 1]


class ReplaySeriesPort:
    """Serves the recorded answers in the order they were given. A request the recorded run never made, or made fewer
    times, is a divergence, never a guess."""

    def __init__(self, record: RunRecord) -> None:
        self._queues: dict[str, list[SeriesResult]] = {}
        for snap in record.snapshots:
            request = request_from_dict(snap.request)
            values, segments, meta = decode_series_with_meta(record.blobs[snap.data_ref])
            result = SeriesResult(
                request, values, segments, snap.source_query, snap.fingerprint, snap.error,
                str(meta.get("currency") or ""), tuple(meta.get("dropped_filters") or ()), int(meta.get("rows") or 0),
            )
            self._queues.setdefault(snap.key, []).append(result)
        self._served: dict[str, int] = {}

    async def fetch(self, requests: Sequence[SeriesRequest]) -> list[SeriesResult]:
        out: list[SeriesResult] = []
        for request in requests:
            key = request_key(request)
            queue = self._queues.get(key)
            if not queue:
                raise ReplayDivergence(f"the replay made a request the recorded run never made: {request_to_dict(request)}")
            index = self._served.get(key, 0)
            if index >= len(queue):
                raise ReplayDivergence(f"the replay fetched {request_to_dict(request)} more often than the recorded run did")
            self._served[key] = index + 1
            out.append(queue[index])
        return out


@dataclass(frozen=True)
class Outcome:
    events: tuple[dict[str, Any], ...]
    overflow: Mapping[str, Mapping[str, Any]]
    report: dict[str, Any]
    sink_failures: tuple[tuple[int, str], ...] = field(default=())


async def execute(
    runner: Runner,
    *,
    investigation_id: str,
    request: Mapping[str, Any],
    config: DiagnosisConfig,
    seed: int,
    series: SeriesPort,
    runtime: Runtime,
    graph: Graph | None = None,
    extra_sinks: Sequence[EventSink] = (),
    estimator: EffectEstimator | None = None,
) -> Outcome:
    memory = MemorySink()
    emitter = Emitter(investigation_id, runtime.now, [memory, *extra_sinks], cap_bytes=config.payload_cap_bytes)
    with investigation_scope(investigation_id):
        report = await runner.run(
            RunInputs(investigation_id, request, config, seed, series, runtime, emitter, graph, estimator)
        )
    return Outcome(
        tuple(e.to_dict() for e in memory.events), {k: dict(v) for k, v in emitter.overflow.items()}, report,
        tuple(emitter.sink_failures),
    )


async def record_run(
    runner: Runner,
    *,
    investigation_id: str,
    request: Mapping[str, Any],
    config: DiagnosisConfig,
    seed: int,
    series: SeriesPort,
    runtime: Runtime,
    graph: Graph | None = None,
    catalogue_version: str = "",
    extra_sinks: Sequence[EventSink] = (),
    estimator: EffectEstimator | None = None,
) -> RunRecord:
    """Run for real and return the record that lets anyone repeat it offline."""
    recording_series, recording_runtime = RecordingSeriesPort(series), RecordingRuntime(runtime)
    recording_estimator = RecordingEstimator(estimator) if estimator is not None else None
    created = recording_runtime.now()
    outcome = await execute(
        runner, investigation_id=investigation_id, request=request, config=config, seed=seed,
        series=recording_series, runtime=recording_runtime, graph=graph, extra_sinks=extra_sinks,
        estimator=recording_estimator,
    )
    return RunRecord(
        investigation_id=investigation_id, created_at=created, runner=runner.kind, request=dict(request),
        versions={
            "engine": ENGINE_VERSION, "graph": graph.version if graph else "",
            "catalogue": catalogue_version or (graph.catalogue_version if graph else ""), "libs": library_versions(),
        },
        config=config.to_dict(), seed=seed, snapshots=tuple(recording_series.snapshots), blobs=dict(recording_series.blobs),
        events=outcome.events, overflow=outcome.overflow,
        runtime_trace={"clock": recording_runtime.clock, "cancel": recording_runtime.cancel}, report=outcome.report,
        estimates=tuple(recording_estimator.snapshots) if recording_estimator is not None else (),
    )


@dataclass(frozen=True)
class ReplayResult:
    identical: bool
    divergence: str | None
    outcome: Outcome | None


def _first_difference(recorded: Sequence[Mapping[str, Any]], replayed: Sequence[Mapping[str, Any]]) -> str | None:
    for i, (a, b) in enumerate(zip(recorded, replayed, strict=False)):
        if canonical_json(a) != canonical_json(b):
            return f"event {i + 1} differs:\n  recorded {canonical_json(a)}\n  replayed {canonical_json(b)}"
    if len(recorded) != len(replayed):
        return f"the recorded run has {len(recorded)} events, the replay {len(replayed)}"
    return None


def _check_blobs(record: RunRecord) -> None:
    for snap in record.snapshots:
        values, segments = decode_series(record.blobs[snap.data_ref])
        if result_fingerprint(request_from_dict(snap.request), values, segments) != snap.fingerprint:
            raise ReplayDivergence(f"recorded series {snap.data_ref} does not match its fingerprint (record edited or corrupt)")


async def replay(
    record: RunRecord,
    runner_factory: Callable[[str], Runner] | None = None,
    *,
    graph: Graph | None = None,
) -> ReplayResult:
    """Run the record's investigation again from the record alone and compare events and report byte for byte."""
    _check_blobs(record)
    made_by = record.versions.get("engine")
    if made_by != ENGINE_VERSION:
        raise RecordFromAnotherEngine(
            f"the record was made by engine {made_by!r} and this is {ENGINE_VERSION!r}: replay it on the commit that made it "
            "(see docs/DoWhy/REBUILD_PLAN_2026-10-15.md for which commit ran which engine version)"
        )
    if graph is not None and record.versions.get("graph") and graph.version != record.versions["graph"]:
        raise ReplayDivergence(f"the record used graph {record.versions['graph']}, this one is {graph.version}")
    runner = runner_factory(record.runner) if runner_factory else RUNNERS.get(record.runner)()
    config = DiagnosisConfig.from_dict(record.config)
    runtime = ReplayRuntime(record.seed, record.runtime_trace)
    try:
        runtime.now()  # the recorded run read the clock once for ``created_at`` before it began
        outcome = await execute(
            runner, investigation_id=record.investigation_id, request=record.request, config=config, seed=record.seed,
            series=ReplaySeriesPort(record), runtime=runtime, graph=graph,
            estimator=ReplayEstimator(record),
        )
    except ReplayDivergence as exc:
        return ReplayResult(False, str(exc), None)
    difference = _first_difference(record.events, outcome.events)
    if difference is None and canonical_json(record.report) != canonical_json(outcome.report):
        difference = f"the report differs:\n  recorded {canonical_json(record.report)}\n  replayed {canonical_json(outcome.report)}"
    if difference is None and canonical_json(dict(record.overflow)) != canonical_json(dict(outcome.overflow)):
        difference = "the overflowed event payloads differ"
    return ReplayResult(difference is None, difference, outcome)

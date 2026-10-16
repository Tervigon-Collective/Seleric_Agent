"""Run a live diagnosis through the replayable runner and keep the record (plan P2.6, D-46).

The tool gathers its series through a recording series port, then hands the engine input to ``legacy_engine`` here. The
runner is the one a replay uses, so the record is the run itself, not a second run: its events are shown on the live
trace as they happen (``TraceForward``), its clock readings and estimates are recorded, and the series the gather read
are copied into the record from the recording port. Replay re-derives the input from those series by the plan in the
request and must reproduce the same events and report.

Saving the record is best effort in the one way that matters: a store that fails must not fail the answer, but it says
so on the trace and in the log, it is never swallowed."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from seleric_swarm.diagnosis.adapters.catalogue_graph import VendoredGraphAdapter
from seleric_swarm.diagnosis.adapters.legacy_engine_runner import (
    LegacyEngineRunner,
    request_for_plan,
)
from seleric_swarm.diagnosis.adapters.legacy_trace import LegacyTrace, TraceForward
from seleric_swarm.diagnosis.adapters.runtime import SeededRuntime
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.errors import InvalidRequest
from seleric_swarm.diagnosis.domain.gather import GatherPlan
from seleric_swarm.diagnosis.domain.graph import Graph
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput
from seleric_swarm.diagnosis.domain.record import RunRecord
from seleric_swarm.diagnosis.domain.types import SeriesRequest, SeriesResult
from seleric_swarm.diagnosis.ports.estimator import EffectEstimator
from seleric_swarm.diagnosis.ports.runstore import RunStore
from seleric_swarm.diagnosis.trace.active import report_failure
from seleric_swarm.diagnosis.trace.run import RecordingSeriesPort, record_run

_log = logging.getLogger("seleric.diagnosis.trace")
LIVE_SEED = 0  # the engine draws no random numbers a live answer depends on; the seed is recorded all the same


class NoFetch:
    """The series port of a run whose input is already gathered: asking it for data is a bug."""

    async def fetch(self, requests: Sequence[SeriesRequest]) -> list[SeriesResult]:
        raise InvalidRequest("a prepared run fetches no series; the gather step did that before the run")


async def run_recorded(
    inp: DiagnosisInput,
    plan: GatherPlan,
    gathered_through: RecordingSeriesPort,
    *,
    config: DiagnosisConfig,
    estimator: EffectEstimator,
    trace: LegacyTrace | None,
    graph: Graph | None = None,
) -> tuple[Any, RunRecord]:
    """The report (as an object) and the record of a diagnosis run on an already-gathered input."""
    runner = LegacyEngineRunner(prepared=inp)
    record = await record_run(
        runner,
        investigation_id=trace.investigation_id if trace is not None else "inv_untraced",
        request=request_for_plan(plan), config=config, seed=LIVE_SEED, series=NoFetch(),
        runtime=SeededRuntime(LIVE_SEED), graph=graph if graph is not None else VendoredGraphAdapter().load().graph,
        extra_sinks=[TraceForward(trace)] if trace is not None else [], estimator=estimator,
    )
    # The series the gather read, in the order it read them: what the replay's own gather will ask for again.
    record = replace(
        record, snapshots=tuple(gathered_through.snapshots), blobs=dict(gathered_through.blobs),
        subgraph=plan.subgraph.to_dict() if plan.subgraph is not None else {},  # also in the request's plan; a reader need not know
    )
    return runner.report_object, record


def save_record(store: RunStore, record: RunRecord) -> bool:
    """Keep the record; report a failure on the trace and the log, and return whether it was kept."""
    try:
        store.save(record)
    except Exception as exc:
        _log.error("run_record_not_saved investigation_id=%s", record.investigation_id, exc_info=True)
        report_failure("save_run_record", exc, {"investigation_id": record.investigation_id})
        return False
    return True

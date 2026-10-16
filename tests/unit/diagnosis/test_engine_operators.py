"""The engine split into operators (plan P2.1-P2.4): the stages, their registration and chaining, the replayable
legacy-order runner, and the fixture format that is each operator's unit test."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from seleric_swarm.causal import diagnosis as legacy
from seleric_swarm.diagnosis.adapters.legacy_engine_runner import LegacyEngineRunner, request_for
from seleric_swarm.diagnosis.adapters.legacy_estimator import PoolEstimator
from seleric_swarm.diagnosis.adapters.runtime import SeededRuntime
from seleric_swarm.diagnosis.adapters.service_formatting import ServiceFormatting
from seleric_swarm.diagnosis.cli.export_fixture import (
    build_fixture,
    check_fixture,
    main_check,
    main_export,
)
from seleric_swarm.diagnosis.domain.compare import differences
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.inputs import input_from_dict, input_to_dict
from seleric_swarm.diagnosis.domain.investigation import Focus, InvestigationContext
from seleric_swarm.diagnosis.domain.record import RunRecord
from seleric_swarm.diagnosis.domain.types import Scope, Window
from seleric_swarm.diagnosis.operators.base import Ports
from seleric_swarm.diagnosis.operators.engine_ops import ENGINE_INPUT
from seleric_swarm.diagnosis.operators.ledger_view import FindingsLedger
from seleric_swarm.diagnosis.operators.load import register_engine_operators
from seleric_swarm.diagnosis.operators.pipeline import STAGES, EngineEnv, run_stages
from seleric_swarm.diagnosis.operators.summary import summarise
from seleric_swarm.diagnosis.trace.run import record_run, replay
from tests.unit.diagnosis import engine_world as world

pytestmark = pytest.mark.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "diagnosis_ops"


@pytest.fixture(autouse=True)
def in_process_estimates(monkeypatch):
    """Estimates run in this process: the worker pool is a speed-up, not part of what these tests check."""
    monkeypatch.setenv("DIAGNOSIS_WORKERS", "0")


@pytest.fixture(scope="module")
def inp():
    return world.engine_input()


def _env():
    import time

    return EngineEnv(DiagnosisConfig(), PoolEstimator(), ServiceFormatting(), time.monotonic)


class NoSeries:
    async def fetch(self, requests):
        raise AssertionError("the legacy runner reads no series port")


# ------------------------------------------------------------------------------------------ registration
def test_importing_the_stages_registers_no_operator():
    code = (
        "import seleric_swarm.diagnosis.operators.pipeline, seleric_swarm.diagnosis.operators.registry as r;"
        "print(r.OPERATORS.kinds())"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT).stdout
    assert out.strip() == "()"


def test_the_one_explicit_loader_registers_the_four_stages_in_kind_order():
    registry = register_engine_operators()
    assert registry.kinds() == tuple(sorted(STAGES)) and register_engine_operators() is registry


# ------------------------------------------------------------------------------------------ contract
def _ctx_for(inp):
    window = Window(min(inp.event_days), max(inp.event_days))
    from seleric_swarm.diagnosis.adapters.catalogue_graph import VendoredGraphAdapter

    graph = VendoredGraphAdapter().load().graph
    return InvestigationContext(inp.outcome, window, Scope(), graph, DiagnosisConfig(), seed=1), Focus(inp.outcome, Scope(), window)


async def test_each_operator_applies_only_when_what_it_needs_has_been_found(inp):
    from seleric_swarm.diagnosis.domain.investigation import EvidenceBasis, Finding

    registry = register_engine_operators()
    ctx, focus = _ctx_for(inp)
    ledger = FindingsLedger()
    ports = Ports(graph=None, series=NoSeries(), runtime=SeededRuntime(1), estimator=PoolEstimator(), formatting=ServiceFormatting())  # type: ignore[arg-type]

    def verdicts():
        return {k: registry.get(k)().applicable(ctx, focus, ledger).reason_code for k in STAGES}

    assert verdicts() == {k: ("no_engine_input" if k == "is_real" else "no_event_analysis" if k in ("what", "where") else "needs_event_and_what") for k in STAGES}
    ledger.add([Finding(ENGINE_INPUT, EvidenceBasis.PRIOR, inp.outcome, {"input": inp})])
    assert verdicts()["is_real"] == "input_ready"
    for stage in STAGES:
        op = registry.get(stage)()
        assert op.applicable(ctx, focus, ledger).applicable, (stage, verdicts())
        ledger.add((await op.run(ctx, focus, ports, ledger)).findings)
    assert set(verdicts().values()) == {"already_done"}


async def test_an_operator_run_out_of_order_is_refused_loudly(inp):
    from seleric_swarm.diagnosis.domain.errors import InvalidRequest

    ctx, focus = _ctx_for(inp)
    ports = Ports(graph=None, series=NoSeries(), runtime=SeededRuntime(1), estimator=None, formatting=None)  # type: ignore[arg-type]
    with pytest.raises(InvalidRequest, match="no 'event' finding yet"):
        await register_engine_operators().get("what")().run(ctx, focus, ports, FindingsLedger())


# ------------------------------------------------------------------------------------------ stages
def test_the_stages_run_in_the_legacy_order_and_name_themselves_in_the_trace(inp):
    from contextlib import contextmanager

    seen: list[str] = []

    @contextmanager
    def scope(name, inputs):
        seen.append(name)
        yield type("S", (), {"result": {}})()

    import dataclasses

    results = run_stages(inp, dataclasses.replace(_env(), scope=scope))
    assert seen == list(STAGES) and results.report is not None
    assert [r for r in (results.event, results.what, results.dims, results.why) if r is None] == []


def test_stopping_at_a_stage_runs_nothing_after_it(inp):
    part = run_stages(inp, _env(), until="what")
    assert part.what is not None and part.dims is None and part.why is None and part.report is None


def test_the_legacy_entry_point_gives_the_same_report_as_the_stages(inp):
    assert legacy.diagnose(inp).as_dict() == run_stages(inp, _env()).report.as_dict()  # type: ignore[union-attr]


def test_functions_that_now_take_the_configuration_keep_their_old_call_shape(inp):
    found = legacy.discover_identities(inp.outcome, inp.lineage, inp.series, sorted(inp.series[inp.outcome]))
    assert [i.describe() for i in found] == ["m_sales = m_aov × m_orders"]
    assert legacy._val(0.0238, "m_ctr", {"m_ctr": legacy.MetricMeta("m_ctr", unit="ratio")}) == "2.38%"


# ------------------------------------------------------------------------------------------ engine input codec
def test_the_engine_input_round_trips_exactly_and_keeps_non_finite_numbers(inp):
    assert input_from_dict(json.loads(json.dumps(input_to_dict(inp)))) == inp
    odd = world.engine_input()
    day = next(iter(odd.series["m_noise"]))
    odd.series["m_noise"][day] = float("nan")
    back = input_from_dict(json.loads(json.dumps(input_to_dict(odd))))
    assert back.series["m_noise"][day] != back.series["m_noise"][day]  # still NaN


# ------------------------------------------------------------------------------------------ fixtures (P2.4)
@pytest.mark.parametrize("operator", STAGES)
def test_every_operator_has_a_fixture_it_still_reproduces(operator):
    fixture = json.loads((FIXTURES / f"{operator}.json").read_text())
    assert fixture["operator"] == operator
    assert check_fixture(fixture, PoolEstimator()) == []


def test_a_fixture_check_notices_a_changed_answer_and_a_changed_input(inp):
    fixture = build_fixture(inp, DiagnosisConfig(), "is_real", PoolEstimator())
    assert check_fixture(fixture, PoolEstimator()) == []
    wrong = json.loads(json.dumps(fixture))
    wrong["expected"]["event"]["actual"] += 1.0
    assert any("/event/actual" in line for line in check_fixture(wrong, PoolEstimator()))
    edited = json.loads(json.dumps(fixture))
    day = max(edited["input"]["series"]["m_sales"])
    edited["input"]["series"]["m_sales"][day] *= 2
    assert check_fixture(edited, PoolEstimator())


def test_the_comparison_is_exact_except_for_numbers_which_get_a_relative_tolerance():
    assert differences({"a": [1.0, "x"], "b": True}, {"a": [1.0 + 1e-9, "x"], "b": True}, 1e-6) == []
    assert differences({"a": 1.0}, {"a": 1.01}, 1e-6) == ["/a: 1.0 != 1.01"]
    assert differences({"a": "x"}, {"a": "y"}) == ["/a: 'x' != 'y'"]
    assert differences({"a": 1}, {"b": 1}) == ["/a: only in expected", "/b: only in actual"]
    assert differences({"a": True}, {"a": 1}) != []  # a boolean is not a number


# ------------------------------------------------------------------------------------------ runner: record and replay
async def _record(inp, seed=7):
    return await record_run(
        LegacyEngineRunner(), investigation_id="inv_ops", request=request_for(inp), config=DiagnosisConfig(), seed=seed,
        series=NoSeries(), runtime=SeededRuntime(seed), estimator=PoolEstimator(),
    )


async def test_a_recorded_engine_run_replays_byte_identically_through_text(inp):
    record = await _record(inp)
    kinds = [e["kind"] for e in record.events]
    assert kinds[:2] == ["investigation_started", "window_resolved"] and kinds[-1] == "investigation_finished"
    assert [e["payload"]["operator"] for e in record.events if e["kind"] == "operator_started"] == list(STAGES)
    assert record.estimates and record.report is not None and record.report["verdict"] == "explained"
    result = await replay(RunRecord.from_json(record.to_json()), None)
    assert result.identical, result.divergence


async def test_the_replay_serves_the_recorded_estimates_so_it_does_not_depend_on_the_machines_arithmetic(inp):
    record = await _record(inp)
    nudged = json.loads(record.to_json())
    nudged["estimates"][0]["result"]["effect"] += 1e-12  # the recorded estimate, not a fresh one, is what replay reads
    result = await replay(RunRecord.from_dict(nudged), None)
    assert not result.identical  # the report carries the estimate: the replay used the record, and says it differs


async def test_a_replay_of_an_edited_input_reports_a_divergence(inp):
    record = await _record(inp)
    raw = json.loads(record.to_json())
    day = max(raw["request"]["engine_input"]["series"]["m_sales"])
    raw["request"]["engine_input"]["series"]["m_sales"][day] *= 0.5
    result = await replay(RunRecord.from_dict(raw), None)
    assert not result.identical and result.divergence


async def test_an_incomplete_window_ends_the_run_with_the_insufficient_data_report_and_still_replays(inp):
    partial = world.engine_input()
    partial.partial_days = {partial.event_days[0]}
    record = await _record(partial)
    assert record.report is not None and record.report["verdict"] == "insufficient_data"
    assert [e["payload"]["operator"] for e in record.events if e["kind"] == "operator_started"] == ["is_real"]
    assert (await replay(RunRecord.from_json(record.to_json()), None)).identical


async def test_the_runs_clock_readings_come_from_the_runtime_so_a_replay_needs_no_clock(inp):
    record = await _record(inp)
    assert len(record.runtime_trace["clock"]) > len(record.events)  # event stamps, durations, the bridge's time budget


# ------------------------------------------------------------------------------------------ cli
async def test_export_fixture_from_a_recorded_run_reproduces_the_recorded_answer(inp, tmp_path):
    record = await _record(inp)
    source = tmp_path / "record.json"
    source.write_text(record.to_json())
    for operator in STAGES:
        out = tmp_path / f"{operator}.json"
        assert main_export([str(source), "--operator", operator, "--out", str(out)]) == 0
        assert main_check([str(out)]) == 0


def test_the_cli_refuses_a_source_that_is_neither_a_record_nor_an_input(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert main_export([str(bad), "--operator", "what"]) == 2
    assert "neither a run record nor an engine input" in capsys.readouterr().err


def test_summaries_are_plain_data_a_fixture_can_hold(inp):
    results = run_stages(inp, _env())
    for stage in STAGES:
        json.dumps(summarise(stage, results), allow_nan=False, default=str)  # no objects, no non-finite numbers leaking
    assert set(summarise("where", results)) == {"dimensions"}


# ------------------------------------------------------------------------------------------ failures reach the trace
def test_a_step_that_failed_inside_an_estimate_becomes_an_operator_failed_event(inp):
    import dataclasses
    import time
    from datetime import UTC, datetime

    from seleric_swarm.diagnosis.trace import events as kinds
    from seleric_swarm.diagnosis.trace.active import activate_emitter
    from seleric_swarm.diagnosis.trace.emitter import Emitter, MemorySink

    class Failing:
        """The real estimator, plus a failure the worker would have recorded."""

        def estimate_many(self, jobs):
            out = PoolEstimator().estimate_many(jobs)
            for est in out:
                est.failures.append({"site": "naive_association", "error_type": "ValueError", "message": "singular", "traceback": "tb"})
            return out

    memory = MemorySink()
    emitter = Emitter("inv_failures", lambda: datetime.now(UTC), [memory])
    env = EngineEnv(DiagnosisConfig(), Failing(), ServiceFormatting(), time.monotonic)
    with activate_emitter(emitter):
        report = run_stages(inp, dataclasses.replace(env)).report
    failed = [e for e in memory.events if e.kind == kinds.OPERATOR_FAILED]
    assert report is not None and failed
    assert {e.payload["operator"] for e in failed} == {"estimate:naive_association"}
    assert all(e.payload["error_type"] == "ValueError" and e.payload["traceback"] == "tb" for e in failed)
    assert len(failed) == len([d for d in report.drivers if d.n_rows])  # one per driver that was estimated

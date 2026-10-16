"""``python -m seleric_swarm.diagnosis.cli export-fixture <source> --operator <name> [--out file]``: turn a recorded run
(or a bare engine input) into a fixture for one operator, and ``check-fixture`` to run it again (plan P2.4, D-21).

A fixture is the operator's whole input (the engine input and the configuration) with what the operator decided
(``expected``). The stages before it are cheap and pure, so the fixture does not store their outputs: running the
fixture runs the stages up to the named one and compares that stage's summary. The same shape is the unit test of the
operator and, later, an eval case."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from seleric_swarm.diagnosis.adapters.legacy_estimator import PoolEstimator
from seleric_swarm.diagnosis.adapters.service_formatting import ServiceFormatting
from seleric_swarm.diagnosis.domain.compare import differences
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.errors import DiagnosisError, InvalidRequest
from seleric_swarm.diagnosis.domain.inputs import DiagnosisInput, input_from_dict, input_to_dict
from seleric_swarm.diagnosis.domain.record import RunRecord
from seleric_swarm.diagnosis.operators.pipeline import STAGES, EngineEnv, run_stages
from seleric_swarm.diagnosis.operators.summary import summarise
from seleric_swarm.diagnosis.ports.estimator import EffectEstimator
from seleric_swarm.diagnosis.trace.emitter import clean_payload
from seleric_swarm.diagnosis.trace.run import ReplayEstimator

FIXTURE_VERSION = 1
DEFAULT_TOLERANCE = 1e-6  # relative; see domain/compare.py for why estimates need one


def build_fixture(
    inp: DiagnosisInput, config: DiagnosisConfig, operator: str, estimator: EffectEstimator, *, note: str = ""
) -> dict[str, Any]:
    if operator not in STAGES:
        raise InvalidRequest(f"no operator {operator!r}; the operators are {list(STAGES)}")
    env = EngineEnv(config, estimator, ServiceFormatting(), time.monotonic)
    results = run_stages(inp, env, until=operator)
    return {
        "fixture_version": FIXTURE_VERSION, "operator": operator, "note": note, "tolerance": DEFAULT_TOLERANCE,
        "config": config.to_dict(), "input": input_to_dict(inp), "expected": clean_payload(summarise(operator, results)),
    }


def check_fixture(fixture: dict[str, Any], estimator: EffectEstimator) -> list[str]:
    """What differs between the fixture's expected summary and the operator's answer now (empty = the same)."""
    if fixture.get("fixture_version") != FIXTURE_VERSION:
        raise InvalidRequest(f"fixture version {fixture.get('fixture_version')!r}, this reads {FIXTURE_VERSION}")
    operator = fixture["operator"]
    config = DiagnosisConfig.from_dict(fixture["config"])
    env = EngineEnv(config, estimator, ServiceFormatting(), time.monotonic)
    results = run_stages(input_from_dict(fixture["input"]), env, until=operator)
    actual = clean_payload(summarise(operator, results))
    return differences(fixture["expected"], actual, float(fixture.get("tolerance", DEFAULT_TOLERANCE)))


def _load_source(path: Path) -> tuple[DiagnosisInput, DiagnosisConfig, EffectEstimator | None]:
    """A run record of the legacy-order runner, or a bare engine input; and the estimator that reproduces the record."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "snapshots" in raw and "runner" in raw:
        record = RunRecord.from_dict(raw)
        engine_input = record.request.get("engine_input")
        if engine_input is None:
            raise InvalidRequest(f"{path} is a record of runner {record.runner!r}, which keeps no engine input")
        return input_from_dict(engine_input), DiagnosisConfig.from_dict(record.config), ReplayEstimator(record)
    if "series" in raw and "outcome" in raw:
        return input_from_dict(raw), DiagnosisConfig(), None
    raise InvalidRequest(f"{path} is neither a run record nor an engine input")


def main_export(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="diagnosis export-fixture")
    parser.add_argument("source", type=Path, help="a run record JSON (legacy_engine) or an engine input JSON")
    parser.add_argument("--operator", required=True, choices=STAGES)
    parser.add_argument("--out", type=Path, help="where to write the fixture (default: stdout)")
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    try:
        inp, config, replayed = _load_source(args.source)
        fixture = build_fixture(inp, config, args.operator, replayed or PoolEstimator(), note=args.note)
    except (OSError, ValueError, KeyError, DiagnosisError) as exc:
        print(f"cannot export from {args.source}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(fixture, sort_keys=True, indent=1)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"{args.operator}: wrote {args.out} ({len(text)} bytes)")
    else:
        print(text)
    return 0


def main_check(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="diagnosis check-fixture")
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args(argv)
    try:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        found = check_fixture(fixture, PoolEstimator())
    except (OSError, ValueError, KeyError, DiagnosisError) as exc:
        print(f"cannot check {args.fixture}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not found:
        print(f"{fixture['operator']}: as expected")
        return 0
    print(f"{fixture['operator']}: {len(found)} differences", file=sys.stderr)
    for line in found[:20]:
        print("  " + line, file=sys.stderr)
    return 1

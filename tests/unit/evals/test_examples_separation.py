from __future__ import annotations

from pathlib import Path

import pytest

from seleric_swarm.evals import EvalCase, ObservedCase, load_cases, run_suite
from seleric_swarm.evals._case import load_verified_example_ids

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "eval" / "golden"

SHAPE_IDS = {
    "spec-th383-entity-comparison",
    "spec-last3-vs-today",
    "spec-net-sales-yesterday",
    "spec-last-week-roas",
    "spec-why-cac-yesterday",
    "spec-ads-by-platform",
    "spec-followup-keeps-windows",
}


def _runner(case: EvalCase) -> ObservedCase:
    return ObservedCase(id=case.id, status="completed")


def test_golden_dir_loads_shape_cases() -> None:
    ids = {case.id for case in load_cases(GOLDEN_DIR)}
    assert SHAPE_IDS <= ids


def test_entity_comparison_case_pins_shape_and_window_order() -> None:
    cases = {case.id: case for case in load_cases(GOLDEN_DIR)}
    case = cases["spec-th383-entity-comparison"]
    assert case.expect.spec["shape"] == "entity_comparison"
    assert case.expect.spec["entities"][0]["dimension"] == "campaign_name"
    assert [window["role"] for window in case.expect.spec["windows"]] == [
        "event",
        "baseline",
        "context",
    ]


def test_load_verified_example_ids_reads_store(tmp_path: Path) -> None:
    (tmp_path / "seed.yaml").write_text("examples:\n- id: ex-1\n  question: q\n", encoding="utf-8")
    assert load_verified_example_ids(tmp_path) == {"ex-1"}
    assert load_verified_example_ids(tmp_path / "missing-dir") == set()


async def test_case_id_in_examples_store_is_refused(tmp_path: Path) -> None:
    (tmp_path / "seed.yaml").write_text("examples:\n- id: shared\n  question: q\n", encoding="utf-8")
    case = EvalCase.model_validate({"id": "shared", "turns": ["something else"]})
    with pytest.raises(ValueError, match="examples store"):
        await run_suite([case], "replay", runner=_runner, examples_dir=tmp_path)


async def test_disjoint_case_is_allowed(tmp_path: Path) -> None:
    (tmp_path / "seed.yaml").write_text("examples:\n- id: ex-1\n  question: q\n", encoding="utf-8")
    case = EvalCase.model_validate({"id": "case-1", "turns": ["q2"]})
    report = await run_suite([case], "replay", runner=_runner, examples_dir=tmp_path)
    assert report.score.cases[0].id == "case-1"

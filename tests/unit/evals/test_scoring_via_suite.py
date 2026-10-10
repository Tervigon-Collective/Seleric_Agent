from __future__ import annotations

from pathlib import Path

import pytest

from seleric_swarm.evals import EvalCase, ObservedCase, ObservedEvidence, run_suite

CASE = {
    "id": "lookup-net-sales",
    "turns": ["What were net sales yesterday?"],
    "tags": ["lookup"],
    "expect": {
        "status": "completed",
        "spec": {"kind": "analysis", "measures": [{"metric_id": "metric.net_sales"}]},
        "values": [
            {
                "label": "net sales",
                "truth": {"metric_id": "metric.net_sales"},
                "value": 10.0,
                "tolerance": 0.5,
            }
        ],
    },
}


def _good_runner(case: EvalCase) -> ObservedCase:
    return ObservedCase(
        id=case.id,
        status="completed",
        spec={"kind": "analysis", "measures": [{"metric_id": "metric.net_sales"}], "grain": "day"},
        evidence=(ObservedEvidence(metric_id="metric.net_sales", value=10.2),),
        response_text="Net sales were 10.2",
    )


async def test_all_metrics_pass(tmp_path: Path) -> None:
    cases = [EvalCase.model_validate(CASE)]
    report = await run_suite(cases, "replay", runner=_good_runner, out_dir=tmp_path)

    metrics = report.score.cases[0].metrics
    assert metrics["status_ok"] == 1.0
    assert metrics["spec_accuracy"] == 1.0
    assert metrics["value_correctness"] == 1.0
    assert (tmp_path / "report.md").exists()


async def test_value_outside_tolerance_fails() -> None:
    cases = [EvalCase.model_validate(CASE)]

    def runner(case: EvalCase) -> ObservedCase:
        return ObservedCase(
            id=case.id,
            status="completed",
            spec={},
            evidence=(ObservedEvidence(metric_id="metric.net_sales", value=42.0),),
        )

    report = await run_suite(cases, "replay", runner=runner)
    assert report.score.cases[0].metrics["value_correctness"] == 0.0


async def test_status_regression_flagged() -> None:
    cases = [EvalCase.model_validate(CASE)]

    def runner(case: EvalCase) -> ObservedCase:
        return ObservedCase(id=case.id, status="failed", spec={})

    report = await run_suite(cases, "replay", runner=runner, baseline={"status_ok": 1.0})
    assert report.comparison.compared
    assert report.comparison.passed is False
    assert report.comparison.regressions[0].metric == "status_ok"


async def test_missing_metric_is_not_a_regression() -> None:
    cases = [EvalCase.model_validate({"id": "c", "turns": ["q"], "expect": {"status": "completed"}})]

    def runner(case: EvalCase) -> ObservedCase:
        return ObservedCase(id=case.id, status="completed", spec={})

    report = await run_suite(cases, "replay", runner=runner, baseline={"value_correctness": 1.0})
    assert report.score.cases[0].metrics["value_correctness"] is None
    assert report.comparison.passed is True


async def test_replay_without_cassette_raises(tmp_path: Path) -> None:
    cases = [EvalCase.model_validate(CASE)]
    with pytest.raises(FileNotFoundError):
        await run_suite(cases, "replay", cassette_dir=tmp_path)

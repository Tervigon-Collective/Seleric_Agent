"""Catalogue mutations need no agent code change. Five replay cases via the eval harness."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from seleric_swarm.evals import EvalCase, load_cases, run_suite
from seleric_swarm.evals._case import Expect
from seleric_swarm.evals._runner import ObservedCase
from seleric_swarm.semantic import FakeSemanticPort, MetricsQueryArgs

FIXTURE = json.loads(Path("tests/fixtures/catalogue/snapshot.json").read_text(encoding="utf-8"))


def _mutate(case_id: str) -> dict:
    payload = copy.deepcopy(FIXTURE)
    metrics = payload["metrics"]
    if case_id == "rename":
        metrics[0]["id"] = "zz_sales_renamed"
        metrics[0]["label"] = "Renamed Sales"
    elif case_id == "add_dimension":
        metrics[0]["supported_dimensions"] = [*metrics[0]["supported_dimensions"], "zz_added"]
    elif case_id == "hierarchy":
        payload["hierarchies"]["zz_path"]["levels"] = ["zz_coarse", "zz_fine", "zz_new_level"]
    elif case_id == "add_metric":
        metrics.append({
            "id": "zz_new_metric",
            "label": "ZZ New",
            "unit": "ZZU",
            "aggregation": "additive",
            "supported_dimensions": ["zz_tenant"],
        })
    elif case_id == "remove_metric":
        payload["metrics"] = [row for row in metrics if row["id"] != "zz_sales"]
    else:
        raise AssertionError(case_id)
    return payload


def _cases() -> list[EvalCase]:
    specs = [
        ("rename", "completed", ("metric[zz_sales_renamed]", "Renamed Sales"), ("metric[zz_sales]",)),
        ("add_dimension", "completed", ("dim[zz_added]",), ()),
        ("hierarchy", "completed", ("level[zz_new_level]",), ()),
        ("add_metric", "completed", ("metric[zz_new_metric]",), ()),
        ("remove_metric", "failed", ("METRIC_NOT_FOUND",), ("metric[zz_sales]",)),
    ]
    return [
        EvalCase(
            id=case_id,
            turns=("what changed in the catalogue",),
            tags=("semantic_pickup",),
            expect=Expect(status=status, must_mention=mention, forbid=forbid),
        )
        for case_id, status, mention, forbid in specs
    ]


class PickupRunner:
    async def __call__(self, case: EvalCase) -> ObservedCase:
        port = FakeSemanticPort(_mutate(case.id))
        model = port.read_model()
        bits = [f"metric[{metric.id}]" for metric in model.metrics]
        bits += [metric.label for metric in model.metrics]
        for metric in model.metrics:
            bits.extend(f"dim[{dim}]" for dim in metric.supported_dimensions)
        for hierarchy in model.hierarchies:
            bits.extend(f"level[{level}]" for level in hierarchy.levels)
        asked = "zz_sales_renamed" if case.id == "rename" else "zz_sales"
        response = await port.query(MetricsQueryArgs(measures=(asked,)))
        if response.error is not None:
            bits.append(response.error.code)
            status = "failed"
        else:
            status = "completed"
        return ObservedCase(id=case.id, status=status, question=case.turns[0], response_text=" ".join(bits))


@pytest.mark.asyncio
async def test_five_replay_cases_follow_a_mutated_catalogue() -> None:
    cases = _cases()
    assert len(cases) == 5
    report = await run_suite(cases, mode="replay", runner=PickupRunner(), suite="semantic_pickup")
    assert report.case_count == 5
    for score in report.score.cases:
        assert score.metrics["status_ok"] == 1.0, score
        assert "missing_mentions" not in score.details, score
        assert "forbidden_hits" not in score.details, score


def test_pickup_cases_are_not_in_the_golden_loader_by_accident() -> None:
    """Eval cases and the pickup mutations stay separate from the golden files."""
    golden = Path("eval/golden")
    if not golden.exists():
        return
    loaded = {case.id for case in load_cases(golden)}
    assert loaded.isdisjoint({case.id for case in _cases()})

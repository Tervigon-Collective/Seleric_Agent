"""forecast_metrics tool wiring (compile + pipeline stub)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from seleric_swarm.agent.output import ToolResult
from seleric_swarm.forecasting.pipeline import ForecastOutcome
from seleric_swarm.forecasting.types import ForecastPlan, HorizonWindow
from seleric_swarm.toolsets import forecasting as tool


def _ctx() -> MagicMock:
    deps = MagicMock()
    deps.catalogue = MagicMock()
    deps.catalogue.label_for = MagicMock(return_value=None)
    deps.catalogue.has_metric = MagicMock(return_value=True)
    deps.catalogue.carries = MagicMock(return_value=True)
    deps.catalogue.supported_dimensions_for = MagicMock(return_value=[])
    deps.catalogue.date_basis_for = MagicMock(return_value=(None, None))
    deps.catalogue.unit_for = MagicMock(return_value=None)
    deps.catalogue.metrics = ()
    deps.mission_id = "m1"
    deps.principal = SimpleNamespace(workspace_id="w1")
    deps.required_scope = None
    deps.as_of = date(2026, 10, 9)
    deps.context = SimpleNamespace(settings=SimpleNamespace(chronos_base_url=""), timezone="Asia/Kolkata")
    deps.canonical_metric_id = None
    deps.mcp_client = MagicMock()
    deps.artifact_store = MagicMock()
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


@pytest.mark.asyncio
async def test_forecast_metrics_refuses_empty_targets():
    res = await tool.forecast_metrics(_ctx(), targets=[])
    assert isinstance(res, ToolResult)
    assert not res.success
    assert res.error_code == "INSUFFICIENT_EVIDENCE"


@pytest.mark.asyncio
async def test_forecast_metrics_runs_pipeline():
    plan = ForecastPlan(
        targets=[],
        horizon=HorizonWindow(start=date(2026, 10, 10), end=date(2026, 10, 23), n_days=14),
        as_of=date(2026, 10, 9),
        cutoff=date(2026, 10, 7),
    )
    art = MagicMock()
    art.evidence_ids = ["e1"]
    art.model_id = "chronos-2"
    art.model_version = "1"
    art.status = "provisional"
    art.input_hash = "abc"
    art.entity = {}
    outcome = ForecastOutcome(
        plan=plan,
        artifact=art,
        artifact_id="f1",
        prediction_ids=["p1"],
        skeleton="ANSWER SKELETON:\nok",
        refused=False,
    )
    with (
        patch.object(tool, "compile_forecast_plan", new=AsyncMock(return_value=plan)) as compile_mock,
        patch.object(tool, "run_forecast", new=AsyncMock(return_value=outcome)) as run_mock,
    ):
        res = await tool.forecast_metrics(
            _ctx(),
            targets=["net_sales"],
            horizon_days=14,
            entity_dimension="lt_platform",
            entity_values=["meta"],
        )
    assert res.success
    assert res.artifact_ids == ["f1", "p1"]
    assert "ANSWER SKELETON" in res.summary
    compile_mock.assert_awaited_once()
    run_mock.assert_awaited_once()

"""Shared fixtures for the diagnosis tests: the eval world served as a semantic layer, and the two series adapters."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "eval" / "diag" / "fixtures" / "brand20_daily_2026-10.json"
sys.path.insert(0, str(ROOT / "eval" / "diag"))
import inject
import runner
import world_fixture as wf

from seleric_swarm.diagnosis.adapters.cube_series import CubeSeriesAdapter
from seleric_swarm.diagnosis.adapters.fixtures import (
    FixtureSeriesAdapter,
    load_series_data,
)


def build_cube_world():
    from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
    from seleric_swarm.conversations.contracts import ContextBundle, Principal
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
    from seleric_swarm.state.artifacts import InMemoryArtifactStore

    fx = wf.load_fixture("brand20_daily_2026-10")
    world = inject.apply_injections(fx, (), runner.json.loads(runner.GRAPH_PATH.read_text()))
    dims_of = {m: [runner.SCOPE_DIMENSION, *sorted(world.segments.get(m, {}))] for m in fx.definitions}
    metrics = tuple(
        CatalogueMetricMeta(
            id=m, label=m, view=str((d.get("cube_mapping") or {}).get("view") or ""),
            supported_dimensions=dims_of[m], raw=d,
        )
        for m, d in sorted(fx.definitions.items()) if m in world.series
    )
    mcp = runner.WorldMcp(world, fx.definitions)
    deps = SelericDeps(
        mission_id="t", as_of=datetime(2026, 10, 1, 12, tzinfo=UTC),
        principal=Principal(principal_id="t", workspace_id="t", user_id="t"), thread_id="t", run_id="r", trace_id="tr",
        context=ContextBundle(), mcp_client=mcp, artifact_store=InMemoryArtifactStore(), limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(metrics=metrics, dimensions=tuple(sorted({x for v in dims_of.values() for x in v}))),
    )

    class Ctx:
        def __init__(self) -> None:
            self.deps = deps

    return Ctx(), mcp


@pytest.fixture
def cube_world():
    return build_cube_world()


@pytest.fixture
def cube_adapter(cube_world):
    return CubeSeriesAdapter(cube_world[0])


@pytest.fixture(scope="session")
def file_adapter():
    return FixtureSeriesAdapter(load_series_data(FIXTURE))

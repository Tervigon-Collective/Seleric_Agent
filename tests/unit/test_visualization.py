from __future__ import annotations

from datetime import UTC, datetime
import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.analytics.visualization import generate_visualization_spec
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance, ContextBundle, Principal
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import analytics


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(store: InMemoryArtifactStore | None = None) -> SelericDeps:
    return SelericDeps(
        mission_id="mission-viz-1",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="thread-viz-1",
        run_id="run-viz-1",
        trace_id="trace-viz-1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=store or InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )


def _make_evidence(
    metric_id: str,
    start: str,
    end: str,
    value: float,
    unit: str = "inr",
    grain: str = "day",
    dimensions: dict[str, str] | None = None,
) -> EvidenceArtifact:
    return EvidenceArtifact(
        metric_id=metric_id,
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        period_start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        period_end=datetime.fromisoformat(end).replace(tzinfo=UTC),
        value=value,
        grain=grain,  # type: ignore[arg-type]
        unit=unit,
        dimensions=dimensions or {},
        source_query={"measure": metric_id},
    )


def _put_evidence(store: InMemoryArtifactStore, evidence: EvidenceArtifact) -> str:
    art = store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="evidence",
            payload=evidence.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{evidence.metric_id}:{evidence.period_start.isoformat()}"],
            provenance=ArtifactProvenance(query_version="q1"),
        )
    )
    return art.id


def test_visualization_spec_empty():
    res = generate_visualization_spec([], "trend")
    assert "error" in res


def test_visualization_spec_single_point_refusal():
    evidence = [
        _make_evidence("metric.net_sales", "2026-09-01T00:00:00", "2026-09-01T23:59:59", 100.0)
    ]
    res = generate_visualization_spec(evidence, "trend")
    assert "error" in res
    assert "Insufficient data" in res["error"]


def test_visualization_spec_trend():
    evidence = [
        _make_evidence("metric.net_sales", "2026-09-01T00:00:00", "2026-09-01T23:59:59", 100.0),
        _make_evidence("metric.net_sales", "2026-09-02T00:00:00", "2026-09-02T23:59:59", 150.0),
    ]
    spec = generate_visualization_spec(evidence, "trend over time", title="Sales Trend")
    assert spec["chart_type"] == "line"
    assert spec["title"] == "Sales Trend"
    assert spec["xAxis"]["type"] == "category"
    assert len(spec["data"]) == 2
    assert spec["data"][0]["time"] == "2026-09-01"
    assert len(spec["series"]) == 1
    assert spec["series"][0]["type"] == "line"


def test_visualization_spec_composition_pie():
    evidence = [
        _make_evidence(
            "metric.orders",
            "2026-09-01T00:00:00",
            "2026-09-30T23:59:59",
            float(val),
            unit="orders",
            grain="month",
            dimensions={"channel": ch},
        )
        for val, ch in [(50, "meta"), (30, "google"), (20, "direct")]
    ]
    spec = generate_visualization_spec(evidence, "share of orders", title="Channel Share")
    assert spec["chart_type"] == "pie"
    assert len(spec["data"]) == 3
    assert spec["series"][0]["type"] == "pie"


def test_visualization_spec_multi_axis_units():
    evidence = [
        _make_evidence("metric.net_sales", "2026-09-01T00:00:00", "2026-09-01T23:59:59", 1000.0, unit="inr"),
        _make_evidence("metric.conversion_rate", "2026-09-01T00:00:00", "2026-09-01T23:59:59", 2.5, unit="pct"),
        _make_evidence("metric.net_sales", "2026-09-02T00:00:00", "2026-09-02T23:59:59", 1200.0, unit="inr"),
        _make_evidence("metric.conversion_rate", "2026-09-02T00:00:00", "2026-09-02T23:59:59", 3.1, unit="pct"),
    ]
    spec = generate_visualization_spec(evidence, "trend", title="Sales vs Conversion")
    assert len(spec["yAxis"]) == 2
    assert spec["yAxis"][0]["position"] == "left"
    assert spec["yAxis"][1]["position"] == "right"


@pytest.mark.asyncio
async def test_generate_visualization_tool():
    store = InMemoryArtifactStore()
    deps = _deps(store)
    ctx = FakeRunContext(deps)

    e1 = _make_evidence("metric.revenue", "2026-09-01T00:00:00", "2026-09-01T23:59:59", 500.0)
    e2 = _make_evidence("metric.revenue", "2026-09-02T00:00:00", "2026-09-02T23:59:59", 650.0)

    id1 = _put_evidence(store, e1)
    id2 = _put_evidence(store, e2)

    result = await analytics.generate_visualization(
        ctx, evidence_ids=[id1, id2], intent="trend", title="Revenue Trend"
    )
    assert result.success is True
    assert len(result.artifact_ids) == 1

    chart_artifact = store.get(result.artifact_ids[0])
    assert chart_artifact is not None
    assert chart_artifact.artifact_type == "chart_spec"
    assert chart_artifact.classification == "derived"
    assert chart_artifact.payload["chart_type"] == "line"

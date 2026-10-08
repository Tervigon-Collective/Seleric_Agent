from __future__ import annotations

from datetime import UTC, datetime
import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
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
    spec = generate_visualization_spec(evidence, title="Sales Trend", chart_type="line")
    assert spec["chart_type"] == "line"
    assert spec["title"] == "Sales Trend"
    assert spec["xAxis"]["type"] == "category"
    assert len(spec["data"]) == 2
    assert spec["data"][0]["time"] == "2026-09-01"
    assert len(spec["series"]) == 1
    assert spec["series"][0]["type"] == "line"


def test_visualization_spec_bar_over_time():
    """Per-day bars must keep each day as its own category — not sum into one bar."""
    evidence = [
        _make_evidence("metric.net_profit", "2026-10-02T00:00:00", "2026-10-02T23:59:59", 6944.0),
        _make_evidence("metric.net_profit", "2026-10-03T00:00:00", "2026-10-03T23:59:59", -12889.0),
        _make_evidence("metric.net_profit", "2026-10-04T00:00:00", "2026-10-04T23:59:59", 11483.0),
    ]
    spec = generate_visualization_spec(
        evidence,
        intent="stacked bar graph for the net profit waterfall",
        title="Net profit waterfall",
        chart_type="bar",
    )
    assert spec["chart_type"] == "bar"
    assert spec["xAxis"]["key"] == "time"
    assert len(spec["data"]) == 3
    assert spec["data"][0]["time"] == "2026-10-02"
    assert spec["series"][0]["type"] == "bar"


def test_visualization_spec_ignores_intent_keywords_without_chart_type():
    """Without an explicit/Jev chart_type, multi-period evidence defaults to line
    even when the intent string contains 'bar' — no keyword matching."""
    evidence = [
        _make_evidence("metric.net_profit", "2026-10-02T00:00:00", "2026-10-02T23:59:59", 100.0),
        _make_evidence("metric.net_profit", "2026-10-03T00:00:00", "2026-10-03T23:59:59", 200.0),
    ]
    spec = generate_visualization_spec(evidence, intent="stacked bar waterfall per day")
    assert spec["chart_type"] == "line"


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
    spec = generate_visualization_spec(evidence, title="Channel Share", chart_type="pie")
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
    spec = generate_visualization_spec(evidence, title="Sales vs Conversion", chart_type="line")
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
    # Jev unconfigured → structural default for multi-period evidence is line
    assert chart_artifact.payload["chart_type"] == "line"


@pytest.mark.asyncio
async def test_generate_visualization_uses_jev_chart_choice(monkeypatch):
    from seleric_swarm.agent.dependencies import JevConfig

    store = InMemoryArtifactStore()
    deps = _deps(store)
    deps = SelericDeps(
        mission_id=deps.mission_id,
        as_of=deps.as_of,
        principal=deps.principal,
        thread_id=deps.thread_id,
        run_id=deps.run_id,
        trace_id=deps.trace_id,
        context=deps.context,
        mcp_client=deps.mcp_client,
        artifact_store=store,
        limits=deps.limits,
        jev=JevConfig(base_url="http://jev.test", api_key="k", timeout=1.0),
    )
    ctx = FakeRunContext(deps)

    id1 = _put_evidence(
        store, _make_evidence("metric.net_profit", "2026-10-02T00:00:00", "2026-10-02T23:59:59", 100.0)
    )
    id2 = _put_evidence(
        store, _make_evidence("metric.net_profit", "2026-10-03T00:00:00", "2026-10-03T23:59:59", -50.0)
    )

    async def _fake_select(intent, **kwargs):
        assert "stacked bar" in intent
        assert "time_periods=2" in kwargs["data_shape"]
        return "bar"

    monkeypatch.setattr(analytics, "select_chart_type", _fake_select)

    result = await analytics.generate_visualization(
        ctx,
        evidence_ids=[id1, id2],
        intent="stacked bar graph for the net profit waterfall for the last 7 days per day",
        title="Net profit waterfall",
    )
    assert result.success is True, result.summary
    chart = store.get(result.artifact_ids[0])
    assert chart is not None
    assert chart.payload["chart_type"] == "bar"
    assert chart.payload["series"][0]["type"] == "bar"
    assert chart.payload["xAxis"]["key"] == "time"


@pytest.mark.asyncio
async def test_generate_visualization_unwraps_finding_to_backing_evidence():
    """Live MS3-7748dee188: model passed a prefetched_lookup finding id; charts
    need the measurements that finding cites, not the derived summary."""
    store = InMemoryArtifactStore()
    deps = _deps(store)
    ctx = FakeRunContext(deps)

    id1 = _put_evidence(
        store, _make_evidence("metric.net_profit", "2026-10-01T00:00:00", "2026-10-01T23:59:59", 100.0)
    )
    id2 = _put_evidence(
        store, _make_evidence("metric.net_profit", "2026-10-02T00:00:00", "2026-10-02T23:59:59", 200.0)
    )
    finding = Finding(
        finding_type="prefetched_lookup",
        statement="Values fetched by the planner's executor for 2026-10-01..2026-10-02.",
        evidence_ids=[id1, id2],
        metrics={"net_profit | 2026-10-01": 100.0, "net_profit | 2026-10-02": 200.0},
    )
    finding_id = store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="finding",
            payload=finding.model_dump(mode="json"),
            classification="derived",
            evidence_ids=[id1, id2],
            provenance=ArtifactProvenance(evidence_ids=[id1, id2], calculation_version="executor.v1"),
            mission_id=deps.mission_id,
        )
    ).id

    result = await analytics.generate_visualization(
        ctx, evidence_ids=[finding_id], intent="trend", title="Net profit"
    )
    assert result.success is True, result.summary
    chart = store.get(result.artifact_ids[0])
    assert chart is not None
    assert chart.artifact_type == "chart_spec"
    assert set(chart.evidence_ids) == {id1, id2}
    assert chart.payload["chart_type"] == "line"


def test_visualization_spec_varying_dimensions_exclude_constant_filter():
    """Constant filter dimensions (e.g. platform='meta' or a list of filtered IDs)
    must not pollute the series key when an entity dimension (e.g. campaign_name) varies."""
    evidence = [
        _make_evidence(
            "metric.ctr", "2026-09-01T00:00:00", "2026-09-01T23:59:59", 0.05,
            dimensions={"campaign_name": "Dog Harness", "platform": "meta", "account_id": "12345"},
        ),
        _make_evidence(
            "metric.ctr", "2026-09-02T00:00:00", "2026-09-02T23:59:59", 0.06,
            dimensions={"campaign_name": "Dog Harness", "platform": "meta", "account_id": "12345"},
        ),
        _make_evidence(
            "metric.ctr", "2026-09-01T00:00:00", "2026-09-01T23:59:59", 0.03,
            dimensions={"campaign_name": "Snugboo", "platform": "meta", "account_id": "12345"},
        ),
        _make_evidence(
            "metric.ctr", "2026-09-02T00:00:00", "2026-09-02T23:59:59", 0.04,
            dimensions={"campaign_name": "Snugboo", "platform": "meta", "account_id": "12345"},
        ),
    ]
    spec = generate_visualization_spec(evidence, title="Campaign CTR", chart_type="line")
    assert spec["chart_type"] == "line"
    series_names = [s["name"] for s in spec["series"]]
    assert series_names == ["Dog Harness", "Snugboo"]
    assert "meta" not in series_names[0]
    assert "12345" not in series_names[0]


def test_visualization_spec_multimetric_with_varying_dimensions():
    evidence = [
        _make_evidence(
            "metric.ctr", "2026-09-01T00:00:00", "2026-09-01T23:59:59", 0.05,
            dimensions={"campaign_name": "Dog Harness", "platform": "meta"}, unit="pct",
        ),
        _make_evidence(
            "metric.cpa", "2026-09-01T00:00:00", "2026-09-01T23:59:59", 20.0,
            dimensions={"campaign_name": "Dog Harness", "platform": "meta"}, unit="inr",
        ),
        _make_evidence(
            "metric.ctr", "2026-09-02T00:00:00", "2026-09-02T23:59:59", 0.06,
            dimensions={"campaign_name": "Snugboo", "platform": "meta"}, unit="pct",
        ),
        _make_evidence(
            "metric.cpa", "2026-09-02T00:00:00", "2026-09-02T23:59:59", 22.0,
            dimensions={"campaign_name": "Snugboo", "platform": "meta"}, unit="inr",
        ),
    ]
    spec = generate_visualization_spec(evidence, title="CTR and CPA", chart_type="line")
    series_names = [s["name"] for s in spec["series"]]
    assert "Ctr (Dog Harness)" in series_names
    assert "Cpa (Dog Harness)" in series_names
    assert "meta" not in series_names[0]


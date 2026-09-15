"""Phase 1 of retiring lookup_v1 (docs/features/business-state-service/
05_SPRINT_PLAN.md): the coordinator-level lookup fast path answers plain
lookup queries via BusinessStateService directly, with no agent-to-agent
handoff -- structurally immune to the metric-id ping-pong bug that caused
a live "how is attribution doing ... per channel" query to hit
HANDOFF_REJECTED after 4 handoffs.
"""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator import lookup_fast_path
from seleric_swarm.coordinator.contracts import DomainQuestion, NormalizedQuery, TimeRange
from seleric_swarm.domain.models import MetricState
from seleric_swarm.swarm.providers.base import DataResult, MetricReading


def _normalized(**overrides) -> NormalizedQuery:
    defaults = dict(
        original_query="how is attribution doing",
        intents=["lookup"],
        domain_questions=[],
        candidate_domains=[],
    )
    defaults.update(overrides)
    return NormalizedQuery(**defaults)


class _FakeMetricDef:
    def __init__(self, mid: str):
        self.id = mid


class _FakeMetricsRegistry:
    def get(self, metric_id: str):
        return _FakeMetricDef(f"metric.{metric_id}" if not metric_id.startswith("metric.") else metric_id)


def _state(metric_id: str, value: float, status: str = "OK", quality_flags=None) -> MetricState:
    return MetricState(
        metric_id=metric_id,
        catalogue_metric_id=metric_id,
        as_of="2026-09-15",
        actual=value,
        freshness="CURRENT",
        status=status,
        quality_flags=quality_flags or [],
    )


class _FakeBusinessState:
    def __init__(self, states: dict[str, MetricState | list[MetricState]]):
        # A metric_id can map to a list -- consumed in call order, so
        # comparison's two get_metric_state calls (period A, period B) for
        # the same metric_id get different states back.
        self._states = {k: (list(v) if isinstance(v, list) else [v]) for k, v in states.items()}
        self.requests = []

    async def get_metric_state(self, request):
        self.requests.append(request)
        queue = self._states[request.metric_id]
        return queue.pop(0) if len(queue) > 1 else queue[0]


class _FakeStore:
    def put(self, *args, **kwargs):
        pass


def _runtime(states: dict[str, MetricState]):
    from types import SimpleNamespace

    return SimpleNamespace(
        metrics=_FakeMetricsRegistry(),
        business_state=_FakeBusinessState(states),
        store=_FakeStore(),
        mcp=None,
    )


class _FakeDataProvider:
    def __init__(self, result: DataResult | list[DataResult]):
        # A list is consumed in call order -- comparison's two fetch() calls
        # (period A, period B) for the same provider get different results.
        self._results = result if isinstance(result, list) else [result]
        self.calls = []

    async def fetch(self, *, metric_ids, time_range, dimensions=None, limit=None, sort=None):
        self.calls.append({"metric_ids": metric_ids, "time_range": time_range, "dimensions": dimensions})
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


class _FakeProviderBundle:
    def __init__(self, providers: dict):
        self._providers = providers

    def data_for(self, domain: str):
        return self._providers.get(domain)


@pytest.mark.asyncio
async def test_single_domain_lookup(monkeypatch):
    normalized = _normalized(
        primary_metric="net_sales",
        domain_questions=[DomainQuestion(domain="commerce", metrics=["net_sales"], question="q")],
        candidate_domains=["commerce"],
    )
    monkeypatch.setattr(lookup_fast_path, "normalize_query", lambda *a, **k: _async(normalized))
    runtime = _runtime({"net_sales": _state("net_sales", 71727.93)})

    result = await lookup_fast_path.run_lookup_fast_path(runtime, query="what's net sales?")

    assert result is not None
    assert result.status == "completed"
    assert result.mission_lead == "commerce_agent"
    assert result.evidence[0].metric_or_fact == "metric.net_sales"
    assert result.evidence[0].value == 71727.93
    assert result.limitations == []


@pytest.mark.asyncio
async def test_multi_domain_lookup_needs_no_handoff(monkeypatch):
    """The exact shape that broke lookup_v1: metrics owned by two different
    domains in one question, answered in a single pass, no leadership
    transfer at all (there is no leadership to transfer)."""
    normalized = _normalized(
        original_query="how is attribution doing per channel",
        domain_questions=[
            DomainQuestion(domain="attribution", metrics=["attributed_net_revenue"], question="q1"),
            DomainQuestion(domain="funnel", metrics=["events_per_session"], question="q2"),
        ],
        candidate_domains=["attribution", "funnel"],
    )
    monkeypatch.setattr(lookup_fast_path, "normalize_query", lambda *a, **k: _async(normalized))
    runtime = _runtime(
        {
            "attributed_net_revenue": _state("attributed_net_revenue", 44757.57),
            "events_per_session": _state("events_per_session", 11.7),
        }
    )

    result = await lookup_fast_path.run_lookup_fast_path(runtime, query="how is attribution doing per channel")

    assert result is not None
    assert result.status == "completed"
    metric_ids = {row.metric_or_fact for row in result.evidence}
    assert metric_ids == {"metric.attributed_net_revenue", "metric.events_per_session"}
    # Both domains' agent_ids were used, no handoff bookkeeping involved.
    agent_ids = {r.agent_id for r in runtime.business_state.requests}
    assert agent_ids == {"attribution_agent", "funnel_agent"}


@pytest.mark.asyncio
async def test_unavailable_metric_becomes_limitation_not_silent_drop(monkeypatch):
    normalized = _normalized(
        domain_questions=[DomainQuestion(domain="commerce", metrics=["net_sales"], question="q")],
        candidate_domains=["commerce"],
    )
    monkeypatch.setattr(lookup_fast_path, "normalize_query", lambda *a, **k: _async(normalized))
    runtime = _runtime({"net_sales": _state("net_sales", 0.0, status="UNAVAILABLE", quality_flags=["MISSING_DATA"])})

    result = await lookup_fast_path.run_lookup_fast_path(runtime, query="what's net sales?")

    assert result is not None
    assert result.status == "failed"
    assert result.evidence == []
    assert result.limitations == ["metric.net_sales: MISSING_DATA"]


@pytest.mark.asyncio
async def test_unsupported_query_returns_none(monkeypatch):
    normalized = _normalized(unsupported_reason="LLM_CLASSIFICATION_UNAVAILABLE")
    monkeypatch.setattr(lookup_fast_path, "normalize_query", lambda *a, **k: _async(normalized))
    runtime = _runtime({})

    assert await lookup_fast_path.run_lookup_fast_path(runtime, query="???") is None


@pytest.mark.asyncio
async def test_comparison_intent_falls_back(monkeypatch):
    normalized = _normalized(
        intents=["comparison"],
        domain_questions=[DomainQuestion(domain="commerce", metrics=["net_sales"], question="q")],
    )
    monkeypatch.setattr(lookup_fast_path, "normalize_query", lambda *a, **k: _async(normalized))
    runtime = _runtime({"net_sales": _state("net_sales", 1.0)})

    assert await lookup_fast_path.run_lookup_fast_path(runtime, query="compare net sales") is None


@pytest.mark.asyncio
async def test_comparison_computes_period_a_minus_period_b_delta(monkeypatch):
    """Comparison intent is answered once normalize_query actually populates
    comparison_range (period B) -- matches observer.py::_comparison_deltas's
    period_a - period_b convention. Uses the aggregating provider fetch
    (not BusinessStateService, which returns the last daily point, not a
    period total -- see _fetch_period_reading's docstring)."""
    normalized = _normalized(
        intents=["comparison"],
        primary_metric="net_sales",
        domain_questions=[DomainQuestion(domain="commerce", metrics=["net_sales"], question="q")],
        candidate_domains=["commerce"],
        time_range=TimeRange(start="2026-08-01", end="2026-08-01"),
        comparison_range=TimeRange(start="2026-08-02", end="2026-08-02"),
    )
    monkeypatch.setattr(lookup_fast_path, "normalize_query", lambda *a, **k: _async(normalized))
    readings = [
        DataResult(readings=[MetricReading(metric_id="net_sales", value=1000.0)], missing=[]),
        DataResult(readings=[MetricReading(metric_id="net_sales", value=800.0)], missing=[]),
    ]
    fake_provider = _FakeDataProvider(readings)
    bundle = _FakeProviderBundle({"commerce": fake_provider})
    monkeypatch.setattr(lookup_fast_path, "build_hybrid_bundle", lambda **k: (bundle, None))
    runtime = _runtime({})

    result = await lookup_fast_path.run_lookup_fast_path(runtime, query="compare net sales on aug 1 and aug 2")

    assert result is not None
    assert result.status == "completed"
    assert result.query_class == "comparison"
    delta_rows = [row for row in result.evidence if row.metric_or_fact.endswith(".delta")]
    assert len(delta_rows) == 1
    assert delta_rows[0].value == pytest.approx(200.0)  # period_a(1000) - period_b(800)
    assert delta_rows[0].provenance["calculation"] == "period_a - period_b"


@pytest.mark.asyncio
async def test_grained_domain_question_uses_breakdown_fetch(monkeypatch):
    """A detected dimension breakdown (e.g. "per channel") is answered via
    the MCP dimensioned-fetch path (DataProvider.fetch), not
    BusinessStateService -- still no agent-to-agent handoff at all."""
    normalized = _normalized(
        domain_questions=[
            DomainQuestion(domain="attribution", metrics=["attributed_net_revenue"], grain=["channel"], question="q")
        ],
    )
    monkeypatch.setattr(lookup_fast_path, "normalize_query", lambda *a, **k: _async(normalized))
    readings = [
        MetricReading(metric_id="attributed_net_revenue", value=170.0, dimensions={"channel": "meta"}),
        MetricReading(metric_id="attributed_net_revenue", value=84.0, dimensions={"channel": "google"}),
    ]
    fake_provider = _FakeDataProvider(DataResult(readings=readings, missing=[]))
    bundle = _FakeProviderBundle({"attribution": fake_provider})
    monkeypatch.setattr(lookup_fast_path, "build_hybrid_bundle", lambda **k: (bundle, None))
    runtime = _runtime({})

    result = await lookup_fast_path.run_lookup_fast_path(runtime, query="attribution per channel")

    assert result is not None
    assert result.status == "completed"
    assert len(result.evidence) == 2
    channels = {row.dimensions.get("channel") for row in result.evidence}
    assert channels == {"meta", "google"}
    assert all(row.metric_or_fact == "metric.attributed_net_revenue" for row in result.evidence)
    # The dimensioned fetch was requested as a breakdown (empty-string value),
    # matching the existing Observer/domain-agent convention.
    assert fake_provider.calls[0]["dimensions"] == {"channel": ""}


@pytest.mark.asyncio
async def test_breakdown_missing_metric_becomes_limitation(monkeypatch):
    normalized = _normalized(
        domain_questions=[
            DomainQuestion(domain="attribution", metrics=["attributed_net_revenue"], grain=["channel"], question="q")
        ],
    )
    monkeypatch.setattr(lookup_fast_path, "normalize_query", lambda *a, **k: _async(normalized))
    fake_provider = _FakeDataProvider(DataResult(readings=[], missing=["attributed_net_revenue"]))
    bundle = _FakeProviderBundle({"attribution": fake_provider})
    monkeypatch.setattr(lookup_fast_path, "build_hybrid_bundle", lambda **k: (bundle, None))
    runtime = _runtime({})

    result = await lookup_fast_path.run_lookup_fast_path(runtime, query="attribution per channel")

    assert result is not None
    assert result.status == "failed"
    assert result.evidence == []
    assert result.limitations == ["metric.attributed_net_revenue: no data available for the requested breakdown"]


@pytest.mark.asyncio
async def test_mixed_grained_and_ungrained_domain_questions(monkeypatch):
    """One question needs a per-channel breakdown, another is a plain
    aggregate -- both fetch mechanisms run and merge into one result."""
    normalized = _normalized(
        domain_questions=[
            DomainQuestion(domain="attribution", metrics=["attributed_net_revenue"], grain=["channel"], question="q1"),
            DomainQuestion(domain="commerce", metrics=["net_sales"], question="q2"),
        ],
    )
    monkeypatch.setattr(lookup_fast_path, "normalize_query", lambda *a, **k: _async(normalized))
    readings = [MetricReading(metric_id="attributed_net_revenue", value=170.0, dimensions={"channel": "meta"})]
    fake_provider = _FakeDataProvider(DataResult(readings=readings, missing=[]))
    bundle = _FakeProviderBundle({"attribution": fake_provider})
    monkeypatch.setattr(lookup_fast_path, "build_hybrid_bundle", lambda **k: (bundle, None))
    runtime = _runtime({"net_sales": _state("net_sales", 71727.93)})

    result = await lookup_fast_path.run_lookup_fast_path(runtime, query="attribution per channel and net sales")

    assert result is not None
    assert result.status == "completed"
    metric_ids = {row.metric_or_fact for row in result.evidence}
    assert metric_ids == {"metric.attributed_net_revenue", "metric.net_sales"}


async def _async(value):
    return value

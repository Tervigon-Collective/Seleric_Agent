"""Fast-path ready-store: staleness gate + one-call formatter
(business_state_ready_store.md Phase 3)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from seleric_swarm.llm.port import LLMResponse, TokenUsage
from seleric_swarm.services.business_state.formatter import format_business_state, is_stale
from seleric_swarm.services.domain_health.models import DomainStateSnapshot, ResolvedMetric


def _snapshot(computed_at: str, *, status: str = "OK") -> DomainStateSnapshot:
    return DomainStateSnapshot(
        domain="business",
        brand_id="20",
        as_of="2026-09-29",
        computed_at=computed_at,
        status=status,
        metrics=[ResolvedMetric(metric_id="metric.net_sales", value=71727.93, period_delta_pct=-18.0)],
    )


def test_fresh_snapshot_is_not_stale():
    fresh = datetime.now(UTC).isoformat()
    assert is_stale(_snapshot(fresh)) is False


def test_old_snapshot_is_stale():
    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    assert is_stale(_snapshot(old)) is True


def test_unparseable_computed_at_is_stale():
    assert is_stale(_snapshot("not-a-date")) is True


class _FakeLLM:
    def __init__(self):
        self.request = None

    async def complete(self, request):
        self.request = request
        return LLMResponse(text="You did ₹71,728 in net sales.", model="fake", usage=TokenUsage())


class _FakeSettings:
    azure_openai_fast_model = ""
    azure_openai_model = "gpt-fake"
    llm_timeout_s = 30.0


class _FakeRuntime:
    def __init__(self, llm):
        self.llm = llm
        self.settings = _FakeSettings()


@pytest.mark.asyncio
async def test_formatter_passes_snapshot_to_llm_and_returns_text():
    llm = _FakeLLM()
    runtime = _FakeRuntime(llm)
    snapshot = _snapshot(datetime.now(UTC).isoformat())

    answer = await format_business_state(runtime, question="How are we doing?", snapshot=snapshot)

    assert answer == "You did ₹71,728 in net sales."
    # The snapshot's numbers reach the model; internal metric ids do not.
    user_msg = llm.request.messages[-1].content
    assert "71727.93" in user_msg
    assert "metric.net_sales" not in user_msg
    assert "net_sales" in user_msg  # prefix-stripped label


class _FakeRequest:
    """Minimal Request stand-in for calling the endpoint coroutine directly,
    without the auth middleware / TestClient."""

    def __init__(self, runtime):
        self.app = SimpleNamespace(state=SimpleNamespace(runtime_provider=lambda: runtime))
        self.state = SimpleNamespace(request_id="req-test")


def _install_store(monkeypatch, snapshot):
    class _Store:
        async def aget_latest(self, domain):
            return snapshot

    import seleric_swarm.api.business_state as mod

    monkeypatch.setattr(mod, "SnapshotStore", _Store)
    return mod


@pytest.mark.asyncio
async def test_endpoint_serves_from_fresh_snapshot(monkeypatch):
    runtime = _FakeRuntime(_FakeLLM())
    mod = _install_store(monkeypatch, _snapshot(datetime.now(UTC).isoformat()))
    req = mod.BusinessStateRequest(question="How is the business?")

    out = await mod.business_state(req, _FakeRequest(runtime))

    assert out["source"] == "snapshot"
    assert out["answer"] == "You did ₹71,728 in net sales."


@pytest.mark.asyncio
async def test_endpoint_falls_back_to_agent_loop_when_stale(monkeypatch):
    runtime = _FakeRuntime(_FakeLLM())
    old = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    mod = _install_store(monkeypatch, _snapshot(old))

    async def _fake_fallback(rt, *, question, timezone, request):
        return {"answer": "slow answer", "source": "agent_loop", "as_of": None, "freshness": None}

    monkeypatch.setattr(mod, "_fallback_to_agent_loop", _fake_fallback)
    req = mod.BusinessStateRequest(question="How is the business?")

    out = await mod.business_state(req, _FakeRequest(runtime))

    assert out["source"] == "agent_loop"


def test_overview_fast_path_kill_switch(monkeypatch):
    # Routing is by the understand call's kind == "overview", not by phrases; the
    # env kill switch still turns the snapshot answer off.
    from seleric_swarm.agent import runner

    assert runner._business_state_fast_path_enabled() is True
    monkeypatch.setenv("BUSINESS_STATE_FAST_PATH", "0")
    assert runner._business_state_fast_path_enabled() is False

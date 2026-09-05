"""LLM+catalogue classifier for swarm_v2 (coordinator.intake.llm_classifier).

Zero coverage existed for this module before these tests — it's the "rewire
the LLM classifier into swarm_v2" work: replaces regex-only intent/metric
matching with the LLM + live catalogue classifier already used by lookup_v1,
falling back to the offline regex path on any LLM failure (spec: LLM failure
must degrade gracefully, never crash the mission).
"""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.intake import normalize_query
from seleric_swarm.coordinator.intake.llm_classifier import classify_query_via_llm


@pytest.mark.asyncio
async def test_classify_query_via_llm_resolves_diagnostic_intent_and_metric(runtime):
    result = await classify_query_via_llm(
        "Why has CAC increased over the last three days?",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result is not None
    assert "diagnostic" in result.intents
    assert result.primary_metric == "metric.cac"
    assert result.time_range.start is not None


@pytest.mark.asyncio
async def test_classify_query_via_llm_resolves_predictive_and_prescriptive(runtime):
    result = await classify_query_via_llm(
        "What happens if this continues, and what should we do?",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result is not None
    assert "predictive" in result.intents
    assert "prescriptive" in result.intents


@pytest.mark.asyncio
async def test_classify_query_via_llm_maps_coordinator_agent_lead_to_empty(runtime):
    """"coordinator_agent" is the orchestrating role, never a real domain lead -
    callers must see "no lead determined", not a fake domain assignment."""
    result = await classify_query_via_llm(
        "?????",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result is not None
    assert result.domain_lead != "coordinator_agent"


@pytest.mark.asyncio
async def test_classify_query_via_llm_returns_none_when_prompt_missing(runtime, monkeypatch):
    """Missing/broken prompt spec must degrade to None (caller falls back to
    the offline regex classifier), never raise and crash the mission."""

    def _boom(_name: str):
        raise FileNotFoundError("no such prompt")

    monkeypatch.setattr(runtime.prompts, "load", _boom)
    result = await classify_query_via_llm(
        "Why has CAC increased?",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result is None


@pytest.mark.asyncio
async def test_normalize_query_uses_llm_path_when_runtime_given(runtime):
    nq = await normalize_query(
        "Why has CAC increased over the last three days?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
        metrics=runtime.metrics,
        mcp=runtime.mcp,
        runtime=runtime,
    )
    assert "diagnostic" in nq.intents
    assert nq.primary_metric == "metric.cac"


@pytest.mark.asyncio
async def test_normalize_query_falls_back_to_regex_without_runtime(runtime):
    """No runtime given (fixture-mode missions) -> deterministic offline path,
    same intents the regex classifier has always produced."""
    nq = await normalize_query(
        "Why has CAC increased over the last three days?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
        metrics=runtime.metrics,
    )
    assert "diagnostic" in nq.intents
    assert nq.primary_metric == "metric.cac"

"""LLM+catalogue classifier for swarm_v2 (coordinator.intake.llm_classifier).

The classifier grounds intent/metric/entity/domain against the live metric
registry + Seleric catalogue via the LLM. There is no keyword fallback: an
LLM failure surfaces as ``LLM_CLASSIFICATION_UNAVAILABLE`` in the caller's
``NormalizedQuery`` and is treated as an unsupported mission.
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
    """Missing/broken prompt spec must degrade to ``None`` (caller surfaces
    ``LLM_CLASSIFICATION_UNAVAILABLE``), never raise and crash the mission."""

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
        runtime=runtime,
    )
    assert "diagnostic" in nq.intents
    assert nq.primary_metric == "metric.cac"


@pytest.mark.asyncio
async def test_normalize_query_without_runtime_fails_closed(runtime):
    """No runtime given → LLM_CLASSIFICATION_UNAVAILABLE, empty intents.

    The intake no longer has a regex fallback; a missing LLM path means the
    mission is unsupported, not silently reclassified from keywords.
    """
    from seleric_swarm.coordinator.intake import UNSUPPORTED_NO_LLM

    nq = await normalize_query(
        "Why has CAC increased over the last three days?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
        metrics=runtime.metrics,
    )
    assert nq.intents == []
    assert nq.primary_metric is None
    assert nq.unsupported_reason == UNSUPPORTED_NO_LLM


@pytest.mark.asyncio
async def test_classify_query_via_llm_resolves_gs_and_roas_abbreviations(runtime):
    gs = await classify_query_via_llm(
        "Why has gs increased over the last three days?",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert gs is not None
    assert "diagnostic" in gs.intents
    assert gs.primary_metric == "metric.gross_sales"
    assert gs.unsupported_reason is None

    roas = await classify_query_via_llm(
        "Why has roas increased over the last three days?",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert roas is not None
    assert "diagnostic" in roas.intents
    assert roas.primary_metric == "metric.gross_roas"
    assert roas.unresolved is False

"""Jev-signal-driven routing: plan gating (#1) and budget/model tier (#3).

These are pure functions of a QueryClassification, so they test without an LLM
or MCP. The model-tier test checks resolve_v3_model prepends the fast
deployment only when configured and only when prefer_fast is asked for.
"""

from __future__ import annotations

import pytest
from pydantic_ai.models.fallback import FallbackModel

from seleric_swarm.agent import runner
from seleric_swarm.agent.intent import QueryClassification
from seleric_swarm.agent.model import resolve_v3_model
from seleric_swarm.config.settings import Settings


def _qc(**kw) -> QueryClassification:
    return QueryClassification(**kw)


# --- #1 plan gating ------------------------------------------------------------


@pytest.mark.parametrize(
    "classification,expected",
    [
        (_qc(intent="lookup", complexity="simple"), False),
        (_qc(intent="aggregation", complexity="moderate"), False),
        (_qc(intent="diagnostic", complexity="moderate"), True),
        (_qc(intent="causal_investigation"), True),
        (_qc(intent="comparison"), True),
        # complexity escalates even a normally-simple intent
        (_qc(intent="aggregation", complexity="complex"), True),
        # Jev unavailable → no plan (keep the hot path fast)
        (_qc(), False),
    ],
)
def test_should_plan(classification, expected) -> None:
    assert runner._should_plan(classification) is expected


# --- #3 tool budget ------------------------------------------------------------


@pytest.mark.parametrize(
    "intent,ceiling,expected",
    [
        ("lookup", 160, 64),
        ("aggregation", 160, 64),
        ("comparison", 160, 80),
        ("causal_investigation", 160, 128),
        ("causal_investigation", 8, 8),  # never above the configured ceiling
        ("lookup", 3, 3),  # ceiling below the per-intent budget wins
        (None, 160, 160),  # unknown intent → no tightening
        ("unknown_label", 160, 160),
    ],
)
def test_tool_budget(intent, ceiling, expected) -> None:
    assert runner._tool_budget(intent, ceiling) == expected


# --- #3 model tier -------------------------------------------------------------


@pytest.mark.parametrize(
    "classification,expected",
    [
        (_qc(intent="lookup", complexity="simple", needs_write=False), True),
        (_qc(intent="trend", complexity="moderate", needs_write=False), True),
        (_qc(intent="lookup", needs_write=True), False),  # writes use the strong model
        (_qc(intent="lookup", complexity="complex"), False),
        (_qc(intent="diagnostic"), False),
        (_qc(), False),
    ],
)
def test_prefer_fast_model(classification, expected) -> None:
    assert runner._prefer_fast_model(classification) is expected


def _names(model) -> list[str]:
    models = model.models if isinstance(model, FallbackModel) else [model]
    return [getattr(m, "model_name", str(m)) for m in models]


def _settings(fast: str) -> Settings:
    return Settings(
        llm_provider="azure_openai_compatible",
        azure_openai_endpoint="https://x",
        azure_openai_api_key="k",
        azure_openai_models="strong-1,strong-2",
        azure_openai_fast_model=fast,
    )


def test_fast_model_prepended_when_prefer_fast() -> None:
    names = _names(resolve_v3_model(_settings("fast-mini"), prefer_fast=True))
    assert names[0] == "fast-mini"
    assert "strong-1" in names and "strong-2" in names  # strong chain retained


def test_fast_model_not_used_without_prefer_fast() -> None:
    names = _names(resolve_v3_model(_settings("fast-mini")))
    assert "fast-mini" not in names


def test_prefer_fast_is_noop_without_fast_model() -> None:
    names = _names(resolve_v3_model(_settings(""), prefer_fast=True))
    assert "fast-mini" not in names
    assert names[0] == "strong-1"

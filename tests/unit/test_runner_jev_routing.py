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
        ("lookup", 300, 100),
        ("aggregation", 300, 100),
        ("comparison", 300, 150),
        ("causal_investigation", 300, 250),
        ("causal_investigation", 8, 8),  # never above the configured ceiling
        ("lookup", 3, 3),  # ceiling below the per-intent budget wins
        (None, 300, 300),  # unknown intent → no tightening
        ("unknown_label", 300, 300),
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
    model = getattr(model, "wrapped", model)  # PatientModel wraps the chain
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


# --- deterministic relative-date pin (P2 "last month" drift) -----------------

def test_resolved_window_line_pins_bare_last_month() -> None:
    # Live L1-vs-L7: "last month" drifted between the previous full month and
    # the current partial one. The runner now pins it in the system block.
    line = runner._resolved_window_line("net revenue last month", "Asia/Kolkata", "2026-09-25")
    assert "last month" in line
    assert "2026-08-01 through 2026-08-31" in line


def test_resolved_window_line_empty_when_no_relative_phrase() -> None:
    # No relative phrase → nothing pinned (fail-open; explicit dates don't drift).
    assert runner._resolved_window_line("what is net revenue", "Asia/Kolkata", "2026-09-25") == ""


# -- 2026-10-05: small talk is decided without Jev ------------------------------------------------
# Live: Jev labelled "hi" a trend AND a follow-up; in a thread the greeting kept its tools and
# the user got a net-sales figure. Jev also costs ~6s per message.

@pytest.mark.parametrize("text", ["hi", "Hi!", "hello there", "thanks", "thank you", "ok thanks bye", "good morning"])
def test_social_turns_are_small_talk(text):
    from seleric_swarm.agent.runner import _is_small_talk

    assert _is_small_talk(text)


@pytest.mark.parametrize(
    "text",
    ["yes", "ok", "okay", "hi, what were sales yesterday", "thanks, now by channel", "net profit last 30 days", "top 5", "how are sales"],
)
def test_requests_and_acceptances_are_not_small_talk(text):
    from seleric_swarm.agent.runner import _is_affirmation, _is_small_talk

    assert not _is_small_talk(text)
    assert not _is_affirmation("thanks")  # thanks never re-runs the previous offer

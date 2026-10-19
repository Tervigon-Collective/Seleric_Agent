"""Query enhancer — clean output and fail-open behaviour."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from seleric_swarm.llm.port import LLMResponse, TokenUsage
from seleric_swarm.services.query_enhance import _clean_enhanced, enhance_query


def test_clean_strips_quotes_and_labels() -> None:
    assert (
        _clean_enhanced('"What were net sales yesterday?"', "sales?")
        == "What were net sales yesterday?"
    )
    assert (
        _clean_enhanced("Enhanced: Which SKUs had the highest returned units?", "x")
        == "Which SKUs had the highest returned units?"
    )


def test_clean_falls_back_to_original_when_empty() -> None:
    assert _clean_enhanced("   ", "raw draft") == "raw draft"


@pytest.mark.asyncio
async def test_enhance_uses_helper_model_and_returns_cleaned() -> None:
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        text='  "Which product variants had the highest returned units in the last 7 days?"  ',
        model="helper",
        usage=TokenUsage(),
    )
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            azure_openai_helper_model="helper",
            azure_openai_fast_model="",
            llm_timeout_s=30.0,
            primary_model=lambda: "primary",
        ),
        llm=llm,
        metrics=None,
        bootstrap=None,
    )

    out = await enhance_query(runtime, query="which products highest return which variants")
    assert out == "Which product variants had the highest returned units in the last 7 days?"
    assert llm.complete.await_count == 1
    request = llm.complete.await_args.args[0]
    assert request.model == "helper"
    assert request.max_tokens >= 1500
    assert request.timeout_s <= 20.0


@pytest.mark.asyncio
async def test_enhance_raises_on_llm_error() -> None:
    from seleric_swarm.services.query_enhance import QueryEnhanceError

    llm = AsyncMock()
    llm.complete.side_effect = RuntimeError("boom")
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            azure_openai_helper_model="helper",
            azure_openai_fast_model="",
            llm_timeout_s=30.0,
            primary_model=lambda: "primary",
        ),
        llm=llm,
        metrics=None,
        bootstrap=None,
    )
    with pytest.raises(QueryEnhanceError):
        await enhance_query(runtime, query="messy sales question")


@pytest.mark.asyncio
async def test_enhance_raises_on_empty_model_text() -> None:
    from seleric_swarm.services.query_enhance import QueryEnhanceError

    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(text="", model="helper", usage=TokenUsage())
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            azure_openai_helper_model="helper",
            azure_openai_fast_model="",
            llm_timeout_s=30.0,
            primary_model=lambda: "primary",
        ),
        llm=llm,
        metrics=None,
        bootstrap=None,
    )
    with pytest.raises(QueryEnhanceError, match="empty"):
        await enhance_query(runtime, query="give me ad performance")


@pytest.mark.asyncio
async def test_enhance_empty_query() -> None:
    runtime = SimpleNamespace(settings=SimpleNamespace(), llm=AsyncMock())
    assert await enhance_query(runtime, query="  ") == ""

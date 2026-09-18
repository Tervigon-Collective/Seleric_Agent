"""Follow-up time/metric inheritance from conversation context_bundle."""

from __future__ import annotations

import pytest

from seleric_swarm.coordinator.intake import llm_classifier
from seleric_swarm.coordinator.intake.conversation_context import (
    inherit_metric_source,
    inherit_time_range,
    is_business_followup,
    is_incidental_time_chatter,
    leftover_tokens,
)
from seleric_swarm.coordinator.intake.llm_classifier import classify_query_via_llm


@pytest.fixture(autouse=True)
def _clear_classification_cache():
    llm_classifier._CLASSIFICATION_CACHE.clear()


def _user(text: str) -> dict:
    return {"role": "USER", "parts": [{"type": "TEXT", "content": text}]}


def _bundle(*texts: str) -> dict:
    return {"recent_messages": [_user(text) for text in texts]}


def test_inherit_today_from_prior_user_message():
    window = inherit_time_range(
        "gross sale",
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales today"),
    )
    assert window is not None
    assert window.start == "2026-09-18"
    assert window.end == "2026-09-18"
    assert window.relative_token == "today"


def test_explicit_followup_time_is_not_overridden():
    window = inherit_time_range(
        "gross sale yesterday",
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales today"),
    )
    assert window is None


def test_inherit_uses_latest_explicit_window():
    window = inherit_time_range(
        "gross sale",
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales today", "and last 7 days"),
    )
    assert window is not None
    assert window.relative_token == "last_7d"


def test_inherit_skips_small_talk_that_mentions_today():
    window = inherit_time_range(
        "gross sale",
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales yesterday", "thanks that's all for today"),
    )
    assert window is not None
    assert window.relative_token == "yesterday"


def test_inherit_ignores_comparison_window_on_plain_lookup():
    window = inherit_time_range(
        "gross sale",
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("compare net sales 2026-06-01 vs 2026-08-01"),
    )
    assert window is None


def test_inherit_without_prior_window_is_none():
    assert inherit_time_range(
        "gross sale",
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("hello"),
    ) is None


def test_thanks_today_is_incidental_chatter():
    assert is_incidental_time_chatter(
        "thanks that's all for today", "Asia/Kolkata", "2026-09-18"
    )


def test_time_only_followup_is_not_incidental_chatter():
    assert not is_incidental_time_chatter(
        "and last 7 days", "Asia/Kolkata", "2026-09-18"
    )


def test_leftover_tokens_strip_time_and_glue_words():
    assert leftover_tokens("gross sales today") == ["gross", "sales"]
    assert leftover_tokens("yesterday?") == []
    assert leftover_tokens("and last 7 days") == []
    assert leftover_tokens("same thing") == []
    assert leftover_tokens("why?") == []


def test_business_followup_skips_greetings_and_thanks():
    prior = _bundle("gross sales today")
    assert is_business_followup(
        "yesterday?", timezone="Asia/Kolkata", as_of="2026-09-18", context_bundle=prior
    )
    assert is_business_followup(
        "gross sale", timezone="Asia/Kolkata", as_of="2026-09-18", context_bundle=prior
    )
    assert is_business_followup(
        "why?", timezone="Asia/Kolkata", as_of="2026-09-18", context_bundle=prior
    )
    assert not is_business_followup(
        "hi", timezone="Asia/Kolkata", as_of="2026-09-18", context_bundle=prior
    )
    assert not is_business_followup(
        "thanks that's all for today",
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=prior,
    )


def test_inherit_metric_source_skips_time_only_followup():
    source = inherit_metric_source(
        "yesterday?",
        context_bundle=_bundle("gross sales today", "and last 7 days"),
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
    )
    assert source == "gross sales today"


@pytest.mark.asyncio
async def test_classify_followup_inherits_today_instead_of_yesterday_default(runtime):
    result = await classify_query_via_llm(
        "gross sale",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales today"),
    )
    assert result is not None
    assert result.primary_metric == "metric.gross_sales"
    assert result.time_range.start == "2026-09-18"
    assert result.time_range.end == "2026-09-18"
    assert result.time_range.relative_token == "today"


@pytest.mark.asyncio
async def test_classify_time_only_followup_keeps_prior_metric(runtime):
    result = await classify_query_via_llm(
        "yesterday?",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales today"),
    )
    assert result is not None
    assert result.primary_metric == "metric.gross_sales"
    assert result.time_range.relative_token == "yesterday"
    assert result.time_range.start == "2026-09-17"


@pytest.mark.asyncio
async def test_classify_why_followup_keeps_prior_metric(runtime):
    result = await classify_query_via_llm(
        "why?",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales today"),
    )
    assert result is not None
    assert result.primary_metric == "metric.gross_sales"
    assert "diagnostic" in result.intents
    assert result.time_range.relative_token == "today"


@pytest.mark.asyncio
async def test_classify_without_context_defaults_to_yesterday(runtime):
    result = await classify_query_via_llm(
        "gross sale",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
    )
    assert result is not None
    assert result.time_range.relative_token == "yesterday"
    assert result.time_range.start == "2026-09-17"


@pytest.mark.asyncio
async def test_classify_cache_does_not_leak_across_threads(runtime):
    with_today = await classify_query_via_llm(
        "gross sale",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales today"),
    )
    without = await classify_query_via_llm(
        "gross sale",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
    )
    assert with_today is not None and without is not None
    assert with_today.time_range.relative_token == "today"
    assert without.time_range.relative_token == "yesterday"


@pytest.mark.asyncio
async def test_new_metric_followup_does_not_keep_prior_metric(runtime):
    result = await classify_query_via_llm(
        "orders",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales today"),
    )
    assert result is not None
    assert result.primary_metric == "metric.orders"
    assert result.time_range.relative_token == "today"


@pytest.mark.asyncio
async def test_unknown_named_ask_does_not_inherit_prior_metric(runtime):
    result = await classify_query_via_llm(
        "blorptile",
        runtime=runtime,
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=_bundle("gross sales today"),
    )
    assert result is not None
    assert result.primary_metric is None


def test_context_builder_dump_feeds_followup_inherit():
    from seleric_swarm.conversations.context import ContextBuilder
    from seleric_swarm.conversations.contracts import Message, MessagePart, MessageRole, Thread
    from seleric_swarm.conversations.memory import build_in_memory_repositories

    repositories = build_in_memory_repositories()
    thread = repositories.threads.create(Thread(workspace_id="w", owner_user_id="u"))
    repositories.messages.create(
        Message(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            user_id="u",
            role=MessageRole.USER,
            parts=[MessagePart(type="TEXT", content="gross sales today")],
        )
    )
    dumped = ContextBuilder(repositories).build(thread, query="gross sale").model_dump(mode="json")
    window = inherit_time_range(
        "gross sale",
        timezone="Asia/Kolkata",
        as_of="2026-09-18",
        context_bundle=dumped,
    )
    assert window is not None
    assert window.relative_token == "today"
    assert window.start == "2026-09-18"

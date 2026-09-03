import pytest

from seleric_swarm.contracts.lookup import CoordinatorClassificationV1, MetricMappingV1
from seleric_swarm.llm.adapters.fake import FakeLLMAdapter
from seleric_swarm.llm.errors import FallbackDisabled
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata


@pytest.mark.asyncio
async def test_fake_llm_ping():
    llm = FakeLLMAdapter()
    response = await llm.complete(
        LLMRequest(
            messages=[ChatMessage(role="user", content="ping")],
            model="fake",
            metadata=LLMRequestMetadata(prompt_id="ping"),
        )
    )
    assert response.text == "pong"
    assert response.latency_ms >= 0
    assert response.model
    assert "key" not in response.text.lower()


@pytest.mark.asyncio
async def test_fake_llm_classify_structured():
    llm = FakeLLMAdapter()
    result = await llm.complete_structured(
        LLMRequest(
            messages=[
                ChatMessage(role="system", content="classify"),
                ChatMessage(role="user", content="Query: What were net sales on 2026-08-01?\nTimezone: Asia/Kolkata\nAs-of: 2026-09-03"),
            ],
            model="fake",
            metadata=LLMRequestMetadata(prompt_id="coordinator.classify"),
        ),
        CoordinatorClassificationV1,
    )
    assert result.value.query_class == "lookup"
    assert result.value.domain_lead == "commerce_agent"


@pytest.mark.asyncio
async def test_fake_llm_classify_combined_leadership_query():
    llm = FakeLLMAdapter()
    result = await llm.complete_structured(
        LLMRequest(
            messages=[
                ChatMessage(role="system", content="classify"),
                ChatMessage(
                    role="user",
                    content=(
                        "Query: What were CAC and net sales on 2026-08-01?\n"
                        "Timezone: Asia/Kolkata\nAs-of: 2026-09-03"
                    ),
                ),
            ],
            model="fake",
            metadata=LLMRequestMetadata(prompt_id="coordinator.classify"),
        ),
        CoordinatorClassificationV1,
    )
    assert result.value.query_class == "lookup"
    assert result.value.domain_lead == "performance_agent"
    assert "metric.cac" in result.value.metric_hints
    assert "metric.net_sales" in result.value.metric_hints


@pytest.mark.asyncio
async def test_fake_llm_metric_map():
    llm = FakeLLMAdapter()
    result = await llm.complete_structured(
        LLMRequest(
            messages=[
                ChatMessage(
                    role="user",
                    content="Query: What were gross sales yesterday?\nAllowed metric ids: metric.net_sales, metric.gross_sales\nMetric hints: metric.gross_sales",
                )
            ],
            model="fake",
            metadata=LLMRequestMetadata(prompt_id="observer.metric_map"),
        ),
        MetricMappingV1,
    )
    assert result.value.metric_id == "metric.gross_sales"


@pytest.mark.asyncio
async def test_fallback_disabled():
    llm = FakeLLMAdapter()
    with pytest.raises(FallbackDisabled):
        await llm.complete(
            LLMRequest(
                messages=[ChatMessage(role="user", content="ping")],
                model="fake",
                fallback_model="other-model",
            )
        )

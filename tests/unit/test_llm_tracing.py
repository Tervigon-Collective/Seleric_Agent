from __future__ import annotations

import pytest

from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.llm.tracing import (
    enforce_llm_run_metadata,
    llm_run_metadata,
    llm_run_name,
)
from seleric_swarm.observability.tracing import (
    REQUIRED_LLM_RUN_METADATA,
    missing_required_metadata,
)
from seleric_swarm.orchestration.runner import run_mission


def _full_request() -> LLMRequest:
    return LLMRequest(
        messages=[ChatMessage(role="user", content="hi")],
        model="Llama-4-Maverick",
        metadata=LLMRequestMetadata(
            request_id="r1",
            session_id="s1",
            mission_id="m1",
            task_id="t1",
            agent_id="coordinator_agent",
            agent_version="0.1.0",
            prompt_id="coordinator.classify",
            prompt_version="1",
            workflow_name="lookup_v1",
            workflow_version="1.0.0",
            model="Llama-4-Maverick",
        ),
        tags=["coordinator"],
    )


def test_llm_run_metadata_is_complete_for_agent_calls():
    meta = llm_run_metadata(_full_request(), retry_count=0, resolved_model="Llama-4-Maverick")
    assert missing_required_metadata(meta, REQUIRED_LLM_RUN_METADATA) == []
    assert llm_run_name(_full_request()) == "llm.coordinator.classify"


def test_enforce_raises_in_strict_mode_when_incomplete():
    req = _full_request()
    req.metadata.prompt_version = None
    with pytest.raises(ValueError, match="prompt_version"):
        enforce_llm_run_metadata(
            req, llm_run_metadata(req, retry_count=0, resolved_model="x"), strict=True
        )


def test_enforce_is_noop_for_pings_without_prompt_id():
    req = LLMRequest(
        messages=[ChatMessage(role="user", content="ping")],
        model="x",
        metadata=LLMRequestMetadata(request_id="r", agent_id="llm_port"),
    )
    # No prompt_id -> dev-only ping, not subject to full LLM metadata.
    enforce_llm_run_metadata(req, llm_run_metadata(req, retry_count=0, resolved_model="x"), strict=True)


@pytest.mark.asyncio
async def test_adapter_forwards_metadata_to_langsmith_and_enforces(monkeypatch):
    settings = Settings(
        llm_provider="azure_openai_compatible",
        azure_openai_api_key="unit-test-key",
        app_env="test",
        langsmith_tracing=False,
    )
    adapter = AzureOpenAICompatibleAdapter(settings)

    captured: dict = {}

    class _Msg:
        content = "ok"

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Usage:
        prompt_tokens = 1
        completion_tokens = 1
        total_tokens = 2

    class _Completion:
        id = "cmpl-1"
        model = "Llama-4-Maverick"

        def __init__(self) -> None:
            self.choices = [_Choice()]
            self.usage = _Usage()

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _Completion()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    adapter._client = _Client()
    adapter._traced = True

    resp = await adapter.complete(_full_request())
    assert resp.text == "ok"
    extra = captured["langsmith_extra"]
    assert extra["name"] == "llm.coordinator.classify"
    assert missing_required_metadata(extra["metadata"], REQUIRED_LLM_RUN_METADATA) == []


@pytest.mark.asyncio
async def test_every_prompt_backed_llm_call_in_a_mission_has_full_metadata(runtime):
    seen: list[list[str]] = []
    real_complete = runtime.llm.complete
    real_structured = runtime.llm.complete_structured

    def _check(request):
        if request.metadata.prompt_id:
            meta = llm_run_metadata(request, retry_count=0, resolved_model=request.model)
            seen.append(missing_required_metadata(meta, REQUIRED_LLM_RUN_METADATA))

    async def complete(request):
        _check(request)
        return await real_complete(request)

    async def complete_structured(request, schema):
        _check(request)
        return await real_structured(request, schema)

    runtime.llm.complete = complete  # type: ignore[method-assign]
    runtime.llm.complete_structured = complete_structured  # type: ignore[method-assign]

    result = await run_mission(
        runtime,
        query="What were net sales on 2026-08-01?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "completed"
    assert seen, "expected at least one prompt-backed LLM call"
    assert all(missing == [] for missing in seen), seen

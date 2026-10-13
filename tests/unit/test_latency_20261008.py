"""Latency and answer-unit fixes (2026-10-08): stage progress before the agent loop,
one SDK client per loop, the planner's own reasoning effort, catalogue units on
prefetched data."""

from __future__ import annotations

import asyncio
import dataclasses

from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter
from tests.unit.test_planner_executor_context import _catalogue


def _with_units(deps):
    metrics = tuple(
        dataclasses.replace(m, raw={**(m.raw or {}), "unit": unit, "currency_default": ccy})
        for m, unit, ccy in zip(deps.catalogue.metrics, ("INR", "ratio"), ("INR", ""), strict=True)
    )
    return dataclasses.replace(deps, catalogue=dataclasses.replace(_catalogue(), metrics=metrics))






def _settings(**update) -> Settings:
    return Settings(azure_openai_api_key="k", azure_openai_endpoint="https://example.invalid", **update)


def test_one_sdk_client_per_loop_and_connection_settings() -> None:
    async def clients() -> tuple[object, object, object]:
        a = AzureOpenAICompatibleAdapter(_settings()).async_client
        b = AzureOpenAICompatibleAdapter(_settings()).async_client
        c = AzureOpenAICompatibleAdapter(_settings(llm_timeout_s=99.0)).async_client
        return a, b, c

    a, b, c = asyncio.run(clients())
    assert a is b and a is not c
    # A new loop gets its own client: an httpx pool cannot cross loops.
    assert asyncio.run(clients())[0] is not a








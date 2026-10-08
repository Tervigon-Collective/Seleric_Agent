"""Latency and answer-unit fixes (2026-10-08): stage progress before the agent loop,
one SDK client per loop, the planner's own reasoning effort, catalogue units on
prefetched data."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from seleric_swarm.agent import executor, model, progress
from seleric_swarm.agent.plan import MissionPlan, PlanStep
from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter
from tests.unit.test_planner_executor_context import TODAY, _catalogue, _deps, _RecordingMcp


def _with_units(deps):
    metrics = tuple(
        dataclasses.replace(m, raw={**(m.raw or {}), "unit": unit, "currency_default": ccy})
        for m, unit, ccy in zip(deps.catalogue.metrics, ("INR", "ratio"), ("INR", ""), strict=True)
    )
    return dataclasses.replace(deps, catalogue=dataclasses.replace(_catalogue(), metrics=metrics))


@pytest.mark.asyncio
async def test_a_prefetch_reports_its_stage_and_states_each_metrics_unit() -> None:
    deps = _with_units(_deps(_RecordingMcp()))
    events: list[tuple[str, str]] = []
    progress.set_progress_sink(deps.mission_id, lambda kind, summary, payload: events.append((kind, summary)))
    try:
        plan = MissionPlan(shape="lookup", steps=[PlanStep(tool="query_metrics", metric_ids=["spend", "ret"], purpose="fetch")])
        out = await executor.execute_plan(plan, deps, windows=[], as_of=TODAY)
    finally:
        progress.clear_progress_sink(deps.mission_id)
    assert events == [("agent.stage", progress.tool_label("query_metrics"))]
    assert out is not None and out.text.endswith(
        "Units (catalogue): spend=INR, ret=ratio. Label every figure with its unit; the footer's Currency is INR."
    )


@pytest.mark.asyncio
async def test_a_plan_left_to_the_agent_reports_no_prefetch_stage() -> None:
    deps = _deps(_RecordingMcp())
    events: list[str] = []
    progress.set_progress_sink(deps.mission_id, lambda kind, summary, payload: events.append(kind))
    try:
        plan = MissionPlan(shape="why_single_metric", steps=[PlanStep(tool="diagnose_metric_change", metric_ids=["spend"], purpose="why")])
        assert await executor.execute_plan(plan, deps, windows=[], as_of=TODAY) is None
    finally:
        progress.clear_progress_sink(deps.mission_id)
    assert events == []


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


def test_the_planner_runs_at_its_own_reasoning_effort(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(model, "resolve_v3_model", lambda settings, **kw: seen.update(kw) or "m")
    model.resolve_planner_model(
        _settings(azure_openai_planner_model="p", azure_openai_planner_reasoning_effort="low")
    )
    assert seen == {"role_tuning": False, "reasoning_effort": "low"}



def test_a_same_hours_companion_never_replaces_the_full_period_value() -> None:
    """Golden Q6 2026-10-08: Sep 1-8 net sales 696,778 came back with a 00:00-02:00
    companion row (31,102) keyed like it, and the comparison table showed the slice."""
    from seleric_swarm.agent.artifacts import EvidenceArtifact
    from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
    from seleric_swarm.services.elapsed import ELAPSED_KEY
    from seleric_swarm.state.artifacts import InMemoryArtifactStore

    store = InMemoryArtifactStore()

    def put(value: float, dims: dict) -> str:
        payload = EvidenceArtifact(
            metric_id="spend", dimensions=dims, grain="none", as_of=TODAY, period_start=TODAY,
            period_end=TODAY, value=value, source_query={},
        ).model_dump(mode="json")
        return store.put(Artifact(workspace_id="w", artifact_type="evidence", payload=payload,
                                  classification="factual", evidence_ids=["raw"],
                                  provenance=ArtifactProvenance(), mission_id="MS3-x")).id

    ids = [put(696778.29, {}), put(31102.12, {ELAPSED_KEY: "02:00"})]
    deps = _deps(store=store)
    assert [p["value"] for p in executor._payloads(deps, ids)] == [696778.29]
    assert [p["value"] for p in executor._payloads(deps, ids, elapsed=True)] == [31102.12]

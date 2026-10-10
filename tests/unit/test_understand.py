"""The one structured understand call and what the runner derives from it.

- ``classification_from`` maps the reading to the routing signals in code.
- ``understand`` is skipped on the stub model and fails open on errors.
- The runner answers small talk from the reply (no tools, no data), drops value
  filters for words the reading marks as ordinary language, and prefetches a
  lookup's metrics through the executor before the agent loop.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from seleric_swarm.agent import runner
from seleric_swarm.agent.model import _stub_test_model
from seleric_swarm.agent.understand import Understanding, classification_from, understand
from seleric_swarm.api.office import registry
from seleric_swarm.api.v3_state import get_v3_artifact_store, reset_v3_stores
from seleric_swarm.config.settings import Settings
from seleric_swarm.persistence.memory import InMemoryMissionStore
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot


@pytest.fixture(autouse=True)
def _clean_v3_state():
    reset_v3_stores()
    registry.clear()
    yield
    reset_v3_stores()
    registry.clear()


_SLOTS = {"entity_dimension": "", "rank_by": None, "metrics": [], "breakdown_dimensions": [], "breakdown_words": "", "names_period": False}


def _model(payload: dict[str, Any], seen: list[str] | None = None) -> FunctionModel:
    # The slots are required: a model reply states them (empty when the question has none).
    payload = {**_SLOTS, **payload}

    def _func(messages, info):
        if seen is not None:
            seen.append(str(messages[-1].parts[-1].content))
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=payload)])

    return FunctionModel(_func)


def _u(**kw: Any) -> Understanding:
    return Understanding.model_validate({"kind": "analysis", "shape": "lookup", **_SLOTS, **kw})


@pytest.mark.parametrize(
    ("reading", "intent", "complexity"),
    [
        (_u(), "lookup", "simple"),
        (_u(shape="breakdown"), "aggregation", "simple"),
        (_u(shape="period_comparison"), "comparison", "moderate"),
        (_u(shape="entity_comparison"), "comparison", "complex"),
        (_u(shape="why_single_metric"), "diagnostic", "complex"),
        (_u(kind="conversation", shape="other"), "conversation", "simple"),
        (_u(kind="overview", shape="other"), "aggregation", "simple"),
        (_u(kind="what_if", shape="other"), "simulation", "complex"),
        (_u(kind="analysis", shape="other"), None, "simple"),
    ],
)
def test_classification_is_derived_in_code(reading, intent, complexity):
    qc = classification_from(reading)
    assert qc.intent == intent
    assert qc.complexity == complexity
    assert qc.period is None  # windows come from the deterministic time resolver


def test_classification_carries_follow_up_write_and_grain():
    qc = classification_from(_u(kind="action", follows_prior=True, grain="week", direction="decrease"))
    assert qc.needs_write is True and qc.depends_on_prior is True
    assert qc.grain == "week" and qc.direction == "decrease"
    assert classification_from(_u(accepts_offer=True)).depends_on_prior is True
    assert classification_from(None) == runner.QueryClassification()


async def test_understand_skips_the_stub_model_and_fails_open():
    out = await understand(_stub_test_model(), "hi", catalogue=CatalogueSnapshot())
    assert out.understanding is None and out.stats["status"] == "skipped"

    def _boom(messages, info):
        raise RuntimeError("429")

    out = await understand(FunctionModel(_boom), "net sales", catalogue=CatalogueSnapshot())
    assert out.understanding is None and out.stats["status"] == "failed"


async def test_a_reply_without_the_plan_slots_is_not_a_reading():
    """Live 2026-10-07: with every slot defaulted, the planner model returned only
    ``kind`` and each analysis mission ran unplanned (shape other, no metrics). The
    slots are required, so a reply that omits them is asked again, never accepted
    as an empty reading."""
    calls: list[int] = []

    def _func(messages, info):
        calls.append(1)
        args = {"kind": "analysis"} if len(calls) == 1 else {
            "kind": "analysis", "shape": "entity_comparison", "entity_dimension": "campaign_name",
            "names_period": True,
            "rank_by": {"words": "return on ad spend", "metric_id": "net_roas"},
            "metrics": [{"words": "spend", "metric_id": "ad_spend"}], "breakdown_dimensions": [], "breakdown_words": "",
        }
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])

    out = await understand(FunctionModel(_func), "best campaigns vs today", catalogue=CatalogueSnapshot())
    assert len(calls) == 2
    assert out.understanding is not None
    assert out.understanding.shape == "entity_comparison"
    assert [m.metric_id for m in out.understanding.metrics] == ["ad_spend"]


async def test_understand_reads_one_structured_answer_with_its_context():
    seen: list[str] = []
    model = _model({"kind": "analysis", "shape": "lookup", "ordinary_words": ["other"]}, seen)
    out = await understand(
        model,
        "why was meta lower than other days",
        catalogue=CatalogueSnapshot(),
        value_words=["meta", "other"],
        value_meanings={"other": "payment_method = other"},
        prior_question="net sales yesterday",
        prior_offer="Want it by channel?",
    )
    assert out.understanding is not None and out.understanding.ordinary_words == ["other"]
    assert out.stats["status"] == "ok"
    # Each word with where the data records it: "other sources" is not a payment method.
    assert "Data-value words" in seen[0] and "- meta\n- other: recorded as payment_method = other" in seen[0]
    assert "Want it by channel?" in seen[0] and "net sales yesterday" in seen[0]


# --- runner ----------------------------------------------------------------------------


def _runtime(mcp: Any) -> SimpleNamespace:
    return SimpleNamespace(
        settings=Settings(
            llm_provider="fake", v3_agent_enabled=True, azure_openai_api_key="",
            persistence_backend="memory", app_env="test",
        ),
        mcp=mcp,
        store=InMemoryMissionStore(),
    )


class _Mcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        del agent_id
        self.calls.append((capability, arguments))
        if capability == "seleric.catalogue_resolve_concept":
            return {"kind": "resolved_concept", "metric_id": "net_sales"}
        if capability == "seleric.metrics_query":
            return {"query_id": "q1", "rows": [{"net_sales": "71727"}], "provenance": {}}
        return {}


async def _run(monkeypatch, mcp: _Mcp, reading: dict, query: str) -> dict:
    monkeypatch.setattr(runner, "resolve_planner_model", lambda settings: _model(reading))
    return await runner.run_v3_mission(
        _runtime(mcp),  # type: ignore[arg-type]
        query=query, mission_id="MS3-u", workspace_id="default", owner_user_id="default",
        thread_id="thread-u", run_id="run-u", request_id="req-u",
    )


async def test_small_talk_is_answered_from_the_reply_without_data(monkeypatch):
    mcp = _Mcp()
    out = await _run(
        monkeypatch, mcp, {"kind": "conversation", "shape": "other", "reply": "Hi! Ask me about your numbers."}, "hi"
    )
    assert out["result"]["final_response"] == "Hi! Ask me about your numbers."
    assert out["result"]["status"] == "completed"
    assert not [c for c, _ in mcp.calls if c == "seleric.metrics_query"]


async def test_a_short_metric_name_is_resolved_and_prefetched_before_the_loop(monkeypatch):
    # "ns" used to take a hardcoded YAML-alias route; it now resolves through the
    # catalogue's concept resolver and the executor fetches it in code.
    mcp = _Mcp()
    reading = {"kind": "analysis", "shape": "lookup", "metrics": [{"words": "net sales", "metric_id": ""}]}
    await _run(monkeypatch, mcp, reading, "ns")
    queries = [a for c, a in mcp.calls if c == "seleric.metrics_query"]
    assert queries and queries[0]["measures"] == ["net_sales"]
    plans = [a for a in get_v3_artifact_store().list_for_mission("MS3-u") if a.artifact_type == "plan"]
    assert plans and "prefetched data" in plans[0].payload["plan"]
    assert plans[0].payload["stats"]["prefetch"]["queries"] == 1


async def test_ordinary_words_do_not_become_required_filters(monkeypatch):
    captured: dict[str, Any] = {}
    real = runner.run_validated_mission

    async def _capture(agent, deps, prompt, **kw):
        captured["scope"] = deps.required_scope
        return await real(agent, deps, prompt, **kw)

    monkeypatch.setattr(runner, "run_validated_mission", _capture)

    async def _values(runtime, mcp, query):
        term = {
            "term": "other",
            "best_match": "exact",
            "dimensions": [{"dimension": "payment_method", "values": [{"value": "other", "match": "exact"}]}],
        }
        return {"status": "ok", "terms": [term]}

    monkeypatch.setattr(runner, "_resolve_values", _values)
    await _run(monkeypatch, _Mcp(), {"kind": "analysis", "shape": "other"}, "orders paid by other")
    assert [vf.term for vf in captured["scope"].value_filters] == ["other"]
    reading = {"kind": "analysis", "shape": "other", "ordinary_words": ["Other"]}
    await _run(monkeypatch, _Mcp(), reading, "compared to other days")
    assert not captured["scope"].value_filters



async def test_a_forecast_question_is_not_prefetched_so_its_tool_stays_available(monkeypatch):
    mcp = _Mcp()
    reading = {"kind": "forecast", "shape": "trend", "metrics": [{"words": "net sales", "metric_id": ""}]}
    await _run(monkeypatch, mcp, reading, "forecast net sales for next week")
    assert not [c for c, _ in mcp.calls if c == "seleric.metrics_query"]
    plans = [a for a in get_v3_artifact_store().list_for_mission("MS3-u") if a.artifact_type == "plan"]
    assert plans and "prefetch" not in plans[0].payload["stats"]


async def test_a_breakdown_with_no_words_asking_for_it_is_dropped():
    """Two named campaigns read as "by ad platform" became a hard scope requirement and sent the answer back four
    times (live 2026-10-10 thread_d1844eb0): the model must quote the words that ask for the split."""
    quoted = await understand(
        _model({"kind": "analysis", "shape": "breakdown", "breakdown_dimensions": ["channel"], "breakdown_words": "by channel"}),
        "net sales by channel", catalogue=CatalogueSnapshot(),
    )
    assert quoted.understanding is not None and quoted.understanding.breakdown_dimensions == ["channel"]
    unquoted = await understand(
        _model({"kind": "analysis", "shape": "entity_comparison", "breakdown_dimensions": ["ad_platform"], "breakdown_words": " "}),
        "check the metrics for the ads platform", catalogue=CatalogueSnapshot(),
    )
    assert unquoted.understanding is not None and unquoted.understanding.breakdown_dimensions == []

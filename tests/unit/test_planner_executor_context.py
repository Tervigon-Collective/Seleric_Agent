"""Deterministic plan execution, context compaction and figure provenance (2026-10-07)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from seleric_swarm.agent import executor
from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.context import compact_history
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.plan import MissionPlan, PlanStep
from seleric_swarm.agent.validation import EvidenceValidator, _figures
from seleric_swarm.config.settings import helper_chat_model
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.services import elapsed
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore

IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime(2026, 10, 7, tzinfo=IST)


def _catalogue() -> CatalogueSnapshot:
    return CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(id="spend", label="Spend", supported_dimensions=["camp"], raw={"aggregation": "additive"}),
            CatalogueMetricMeta(
                id="ret", label="Return", supported_dimensions=["camp"], raw={"aggregation": "ratio", "volume_metric": "spend"}
            ),
        ),
        dimensions=("camp",),
    )


def _deps(mcp=None, store=None) -> SelericDeps:
    return SelericDeps(
        mission_id="MS3-x",
        as_of=TODAY,
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t",
        run_id="r",
        trace_id="tr",
        context=ContextBundle(),
        mcp_client=mcp,
        artifact_store=store or InMemoryArtifactStore(),
        limits=ExecutionLimits(),
        catalogue=_catalogue(),
    )


class _Mcp:
    """Answers like Cube: hourly rows for granularity=hour, else one row per campaign."""

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        measure = arguments["measures"][0]
        start = arguments["time_range"]["start"]
        days = 3 if start == "2026-10-04" else 1
        camps = ["A", "B", "C"]
        for f in arguments.get("filters") or []:
            if f["dimension"] == "camp" and f["operator"] == "equals":
                camps = f["values"]
        if arguments.get("granularity") == "hour":
            rows = []
            for d in range(days):
                day = f"2026-10-0{4 + d if days == 3 else 7}"
                for hour in range(24):
                    rows += [{"camp": c, "x.hour": f"{day}T{hour:02d}:00:00.000", measure: "10"} for c in camps]
            return {"rows": rows, "provenance": {"query_id": "q"}}
        value = {"A": 3.0, "B": 2.0, "C": 1.0}
        rows = [{"camp": c, measure: str(value[c] if measure == "ret" else 100.0)} for c in camps]
        if arguments.get("sort"):
            rows.sort(key=lambda r: float(r[measure]), reverse=True)
        return {"rows": rows[: arguments.get("limit") or None], "provenance": {"query_id": "q"}}


def _plan() -> MissionPlan:
    return MissionPlan(
        shape="entity_comparison",
        steps=[
            PlanStep(tool="query_metrics", metric_ids=["ret"], dimensions=["camp"], ranking="order='desc', limit=2", purpose="rank"),
            PlanStep(tool="query_metrics", metric_ids=["ret", "spend"], dimensions=["camp"], uses_entities_from_step=1, purpose="ref"),
            PlanStep(tool="query_metrics", metric_ids=["ret", "spend"], dimensions=["camp"], uses_entities_from_step=1, purpose="cmp"),
        ],
    )


@pytest.mark.asyncio
async def test_the_executor_ranks_then_compares_the_same_entities_over_the_same_hours(monkeypatch) -> None:
    monkeypatch.setattr(elapsed, "now_in", lambda tz: datetime(2026, 10, 7, 12, 30, tzinfo=IST))
    deps = _deps(_Mcp())
    out = await executor.execute_plan(
        _plan(), deps, windows=[(date(2026, 10, 4), date(2026, 10, 6)), (date(2026, 10, 7), date(2026, 10, 7))], as_of=TODAY
    )
    assert out is not None and out.finding_id
    # Top 2 by the ratio among the volume leaders; additive spend counts 12 hours x 10 per day.
    assert "A | spend | 120.00 | 120.00 | +0.0%" in out.text
    assert "| C |" not in out.text
    assert "elapsed_only" in out.text and f"finding_ids=[{out.finding_id}]" in out.text


class _RecordingMcp:
    def __init__(self) -> None:
        self.args: list[dict] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.args.append(arguments)
        measure = arguments["measures"][0]
        if arguments.get("dimensions"):
            rows = [{"camp": c, measure: "5"} for c in ("A", "B")]
        else:
            rows = [{measure: "7"}]
        return {"rows": rows, "provenance": {"query_id": "q"}}


@pytest.mark.asyncio
async def test_a_lookup_is_fetched_in_parallel_before_the_loop() -> None:
    mcp = _RecordingMcp()
    plan = MissionPlan(shape="lookup", steps=[PlanStep(tool="query_metrics", metric_ids=["spend", "ret"], purpose="fetch")])
    out = await executor.execute_plan(plan, _deps(mcp), windows=[], as_of=TODAY)
    assert out is not None and out.finding_id and out.stats["queries"] == 2
    assert sorted(a["measures"][0] for a in mcp.args) == ["ret", "spend"]
    # No window named: the tool's own default, the mission day.
    assert {a["time_range"]["start"] for a in mcp.args} == {"2026-10-07"}
    assert "spend=7 " in out.text and f"finding_ids=[{out.finding_id}]" in out.text


@pytest.mark.asyncio
async def test_a_breakdown_only_asks_each_metric_for_dimensions_it_supports() -> None:
    mcp = _RecordingMcp()
    plan = MissionPlan(
        shape="breakdown",
        steps=[PlanStep(tool="query_metrics", metric_ids=["spend"], dimensions=["camp", "nope"], purpose="by camp")],
    )
    out = await executor.execute_plan(plan, _deps(mcp), windows=[(date(2026, 10, 1), date(2026, 10, 6))], as_of=TODAY)
    assert out is not None
    assert mcp.args[0]["dimensions"] == ["camp"]
    assert (mcp.args[0]["time_range"]["start"], mcp.args[0]["time_range"]["end"]) == ("2026-10-01", "2026-10-06")


@pytest.mark.asyncio
async def test_a_trend_defaults_to_daily_buckets() -> None:
    mcp = _RecordingMcp()
    plan = MissionPlan(shape="trend", steps=[PlanStep(tool="query_metrics", metric_ids=["spend"], purpose="trend")])
    await executor.execute_plan(plan, _deps(mcp), windows=[(date(2026, 10, 1), date(2026, 10, 6))], as_of=TODAY)
    assert mcp.args[0].get("granularity") == "day"


@pytest.mark.asyncio
async def test_the_executor_leaves_diagnosis_and_funnels_to_the_agent() -> None:
    for shape, tool in (("why_single_metric", "diagnose_metric_change"), ("funnel", "query_metrics")):
        plan = MissionPlan(shape=shape, steps=[PlanStep(tool=tool, metric_ids=["spend"], purpose="leave it")])
        assert await executor.execute_plan(plan, _deps(_RecordingMcp()), windows=[], as_of=TODAY) is None


def test_old_tool_returns_collapse_to_their_digest_and_the_tail_stays_verbatim() -> None:
    big = {"summary": "spend over 2026-10-04 " + "x" * 2000, "artifact_ids": [f"a{i}" for i in range(30)]}
    messages = [
        ModelRequest(parts=[ToolReturnPart("query_metrics", big, tool_call_id="1")]),
        ModelResponse(parts=[ToolCallPart("query_metrics", {}, tool_call_id="2")]),
        ModelRequest(parts=[ToolReturnPart("query_metrics", big, tool_call_id="2")]),
        ModelResponse(parts=[TextPart("ok")]),
        ModelRequest(parts=[ToolReturnPart("query_metrics", big, tool_call_id="3")]),
        ModelResponse(parts=[TextPart("ok")]),
    ]
    out = compact_history(messages)
    first = out[0].parts[0].content
    assert isinstance(first, str) and first.startswith("[earlier result, compacted]") and "(+18 more)" in first
    assert len(first) < 900
    assert out[-2].parts[0].content == big  # the tail is untouched


def test_numbers_inside_names_are_not_figures() -> None:
    assert _figures("| TH-383-SUSPENDER | 4,845.42 | -17.0% | BN520_TM099 |") == [(4845.42, 0.005, False), (17.0, 0.05, True)]


def _evidence(store, value: float) -> str:
    payload = EvidenceArtifact(
        metric_id="spend", grain="none", as_of=TODAY, period_start=TODAY, period_end=TODAY, value=value, source_query={}
    ).model_dump(mode="json")
    return store.put(
        Artifact(workspace_id="w", artifact_type="evidence", payload=payload, classification="factual",
                 evidence_ids=["raw"], provenance=ArtifactProvenance(), mission_id="MS3-x")
    ).id


def test_invented_figures_are_sent_back_and_real_ones_pass() -> None:
    store = InMemoryArtifactStore()
    aid = _evidence(store, 5821.73)
    good = MissionResult(mission_id="MS3-x", status="completed", query="q", as_of=TODAY,
                         final_response="Net sales so far today are INR 5,821.73.", evidence_ids=[aid])
    assert EvidenceValidator().validate(good, deps=_deps(store=store)).reason is None or "match nothing" not in (
        EvidenceValidator().validate(good, deps=_deps(store=store)).reason or ""
    )
    bad = good.model_copy(update={"final_response": "Sales were INR 5,821.73, then 83,425.35, 61,441.02 and 247,269.57."})
    outcome = EvidenceValidator().validate(bad, deps=_deps(store=store))
    assert not outcome.ok and "match nothing" in (outcome.reason or "") and outcome.self_contradicting


def test_each_role_resolves_its_own_deployment() -> None:
    from types import SimpleNamespace

    assert helper_chat_model(SimpleNamespace(azure_openai_helper_model="h", azure_openai_fast_model="f")) == "h"
    assert helper_chat_model(SimpleNamespace(azure_openai_helper_model="", azure_openai_fast_model="f")) == "f"
    assert helper_chat_model(SimpleNamespace(azure_openai_helper_model="", azure_openai_fast_model="")) == ""


def test_the_planner_gets_a_compact_catalogue() -> None:
    compact = _catalogue().render_compact()
    assert "- spend: Spend" in compact and "Dimensions: camp" in compact
    assert len(compact) < len(_catalogue().render())


class _WindowMcp:
    """One total per window: 300 over Sep 1-3, 450 over Oct 1-3."""

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        measure = arguments["measures"][0]
        value = 450.0 if arguments["time_range"]["start"].startswith("2026-10") else 300.0
        return {"rows": [{measure: str(value)}], "provenance": {"query_id": "q"}}


@pytest.mark.asyncio
async def test_a_period_comparison_judges_the_later_window_against_the_earlier_one() -> None:
    # Live 2026-10-08 golden Q6 "Compare this month to the same number of days last month": this month was named
    # first, became the reference, and every change read backwards ("net sales fell" while they rose).
    plan = MissionPlan(shape="period_comparison", steps=[PlanStep(tool="query_metrics", metric_ids=["spend"], purpose="cmp")])
    out = await executor.execute_plan(
        plan, _deps(_WindowMcp()), windows=[(date(2026, 10, 1), date(2026, 10, 3)), (date(2026, 9, 1), date(2026, 9, 3))],
        as_of=TODAY,
    )
    assert out is not None
    # Windows of equal length compare as totals.
    assert "| metric | 2026-09-01..2026-09-03 | 2026-10-01..2026-10-03 | change |" in out.text
    assert "| spend | 300.00 | 450.00 | +50.0% |" in out.text
    assert "Earlier window 2026-09-01..2026-09-03 vs later window 2026-10-01..2026-10-03" in out.text

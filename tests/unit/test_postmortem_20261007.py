"""Regression tests for the 2026-10-06/07 "best campaigns last 3 days vs today" runs.

MS3-4f7be7ba30 (thread_46f558f4) FAILED after three attempts: a total-audit false
positive, then a malformed completion from the last model in the chain during a
revision threw away a finished draft. MS3-4c6633d347 (thread_f6288227) passed
validation with a false headline ("spend down 73%") built from a 3-day total
against a partial day, and ended with an "(A) or (B)?" offer.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import FunctionModel

from seleric_swarm.agent import model_health
from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.agent.model_health import HealthGatedChatModel, ModelHealth, PatientModel
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.validation import EvidenceValidator, run_validated_mission
from seleric_swarm.agent.validation.answer_audit import replace_metric_ids, total_mismatch
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.services import elapsed
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import semantic

IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime(2026, 10, 7, tzinfo=IST)


def _catalogue() -> CatalogueSnapshot:
    return CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(id="ad_spend", label="Ad spend", raw={"aggregation": "additive"}),
            CatalogueMetricMeta(id="total_sales", label="Total sales", raw={"aggregation": "additive"}),
            CatalogueMetricMeta(id="gross_roas", label="ROAS", raw={"aggregation": "ratio"}),
        ),
        dimensions=("campaign_id", "campaign_name", "ad_name"),
    )


def _deps(store: InMemoryArtifactStore | None = None, *, mcp=None, revisions: int = 1) -> SelericDeps:
    return SelericDeps(
        mission_id="MS3-pm",
        as_of=TODAY,
        principal=Principal(
            principal_id="p1",
            workspace_id="ws-1",
            user_id="user-1",
            authenticated=False,
            auth_method=PrincipalAuthMethod.ANONYMOUS,
        ),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=mcp,
        artifact_store=store or InMemoryArtifactStore(),
        limits=ExecutionLimits(max_validation_revisions=revisions),
        catalogue=_catalogue(),
    )


def _evidence(store, metric_id: str, start: datetime, end: datetime, value: float, dims=None) -> str:
    payload = EvidenceArtifact(
        metric_id=metric_id,
        dimensions=dims or {},
        grain="none",
        as_of=TODAY,
        period_start=start,
        period_end=end,
        value=value,
        source_query={},
    ).model_dump(mode="json")
    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="evidence",
            payload=payload,
            classification="factual",
            evidence_ids=["raw:x"],
            provenance=ArtifactProvenance(),
            mission_id="MS3-pm",
        )
    ).id


@pytest.fixture
def at_noon(monkeypatch):
    """Wall clock 12:30 IST on the mission day: today is in progress."""
    monkeypatch.setattr(elapsed, "now_in", lambda tz: datetime(2026, 10, 7, 12, 30, tzinfo=IST))


# -- answer audit ------------------------------------------------------------------

# Attempt 1's draft, verbatim in the parts that mattered.
_DRAFT = (
    "Lead: For 2026-10-04..2026-10-06 the five campaigns below accounted for ₹99,221.31 total spend "
    "and showed net ROAS ranging from 0.99548 to 2.75 (net ROAS is a ratio and not summable).\n\n"
    "| campaign_id | ad_spend (INR) | net_roas |\n| --- | ---: | ---: |\n"
    "| 120250681379400783 | 29,155.13 | No data available |\n"
    "| 120250695088250783 | 22,121.52 | No data available |\n"
    "| 120250597947340783 | 18,875.21 | 0.99548 |\n"
    "| 120250726752410783 | 14,867.80 | No data available |\n"
    "| 23625200861 | 14,201.65 | 1.83073 |\n\n"
    "What this means: campaign 23889282928 had the highest net_roas (2.75) in the ROAS ranking but "
    "did not appear in the ad_spend top-5 by spend, so its impact on total spend is small."
)


def test_a_far_away_ratio_is_not_read_as_the_total_and_id_columns_are_not_summed() -> None:
    assert total_mismatch(_DRAFT, frozenset({"campaign_id"})) is None
    # A wrong total next to its word is still caught.
    wrong = _DRAFT.replace("₹99,221.31 total", "₹99,999.00 total")
    reason = total_mismatch(wrong, frozenset({"campaign_id"}))
    assert reason is not None and "481,002" not in reason


def test_metric_ids_are_written_as_their_display_names() -> None:
    labels = {"total_sales": "Total sales", "gross_roas": "ROAS"}
    text = "total_sales rose. Today the total_sales figure and gross_roas fell; my_total_sales_x stays."
    out = replace_metric_ids(text, labels)
    assert out == "Total sales rose. Today the total sales figure and ROAS fell; my_total_sales_x stays."


# -- revision loop -------------------------------------------------------------------


def _final(text: str, status: str = "completed", evidence_ids: list[str] | None = None) -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(
                "final_result",
                {"status": status, "final_response": text, "evidence_ids": evidence_ids or []},
            )
        ]
    )


@pytest.mark.asyncio
async def test_a_model_failure_during_a_revision_ships_the_best_draft() -> None:
    store = InMemoryArtifactStore()
    _evidence(store, "ad_spend", TODAY, TODAY, 100.0)
    calls = 0

    def model(_messages, _info) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _final("Spend was 100.", evidence_ids=["artifact_missing"])
        raise UnexpectedModelBehavior("Invalid response from openai chat completions endpoint")

    agent = Agent(FunctionModel(model), deps_type=SelericDeps, output_type=MissionResult)
    result = await run_validated_mission(agent, _deps(store, revisions=3), "q")
    assert result.status == "partial"
    assert result.final_response == "Spend was 100."
    assert result.limitations[0] == "MODEL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_model_failure_without_any_draft_still_fails_the_attempt() -> None:
    def model(_messages, _info) -> ModelResponse:
        raise UnexpectedModelBehavior("boom")

    agent = Agent(FunctionModel(model), deps_type=SelericDeps, output_type=MissionResult)
    with pytest.raises(UnexpectedModelBehavior):
        await run_validated_mission(agent, _deps(), "q")


@pytest.mark.asyncio
async def test_a_leaked_metric_id_is_fixed_without_a_revision() -> None:
    store = InMemoryArtifactStore()
    aid = _evidence(store, "total_sales", TODAY, TODAY, 36014.0)
    agent = Agent(
        FunctionModel(lambda _m, _i: _final("Today total_sales is 36,014.", evidence_ids=[aid])),
        deps_type=SelericDeps,
        output_type=MissionResult,
    )
    result = await run_validated_mission(agent, _deps(store), "q")
    assert result.final_response == "Today total sales is 36,014."
    assert result.trace["validation"]["revisions_used"] == 0


def test_a_partial_answer_that_offers_its_own_remaining_work_is_revised() -> None:
    result = MissionResult(
        mission_id="MS3-pm",
        status="partial",
        query="q",
        as_of=TODAY,
        final_response=(
            "Spend was 100.\n\nWould you like me to (A) fetch the touchpoint drilldown now, or (B) "
            "re-run the diagnosis after the day ends?"
        ),
    )
    outcome = EvidenceValidator().validate(result, deps=_deps())
    assert not outcome.ok and "offering to run the remaining work" in (outcome.reason or "")


# -- like-for-like windows -----------------------------------------------------------


def _spend_answer(store, *, today_dims=None, base_dims=None) -> MissionResult:
    base = _evidence(
        store, "ad_spend", datetime(2026, 10, 4, tzinfo=IST), datetime(2026, 10, 6, tzinfo=IST), 197401.81, base_dims
    )
    today = _evidence(store, "ad_spend", TODAY, TODAY, 26040.83, today_dims)
    return MissionResult(
        mission_id="MS3-pm",
        status="completed",
        query="q",
        as_of=TODAY,
        final_response="Spend is down ~87% today versus the prior 3-day window.",
        evidence_ids=[base, today],
    )


def test_a_change_between_a_3_day_total_and_a_partial_day_is_rejected(at_noon) -> None:
    store = InMemoryArtifactStore()
    outcome = EvidenceValidator().validate(_spend_answer(store), deps=_deps(store))
    assert not outcome.ok and outcome.self_contradicting
    assert "not comparable" in (outcome.reason or "") and "elapsed_only" in (outcome.reason or "")


def test_unequal_complete_windows_are_rejected_too(monkeypatch) -> None:
    # The next day: every window is complete, only the lengths differ.
    monkeypatch.setattr(elapsed, "now_in", lambda tz: datetime(2026, 10, 8, 9, 0, tzinfo=IST))
    store = InMemoryArtifactStore()
    outcome = EvidenceValidator().validate(_spend_answer(store), deps=_deps(store))
    assert not outcome.ok and "3 days" in (outcome.reason or "")
    assert "still in progress" not in (outcome.reason or "")


def test_a_per_day_comparison_over_elapsed_hours_passes(at_noon) -> None:
    store = InMemoryArtifactStore()
    tag = {elapsed.ELAPSED_KEY: "12:00"}
    result = _spend_answer(store, today_dims=tag, base_dims=tag)
    # 197,401.81 over 3 days is 65,800.60 a day; 26,040.83 is 60.4% below it.
    result = result.model_copy(update={"final_response": "Spend is 60% below the per-day average."})
    outcome = EvidenceValidator().validate(result, deps=_deps(store))
    assert outcome.reason is None or "not comparable" not in outcome.reason


# -- query_metrics(elapsed_only=True) ------------------------------------------------


class _Mcp:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.calls.append(arguments)
        return {"rows": self.rows, "provenance": {"query_id": "q1", "currency": "INR"}}


class _Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _hour_rows() -> list[dict]:
    rows = []
    for day in ("2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07"):
        for hour in range(24 if day != "2026-10-07" else 12):
            rows.append({"report_hour.hour": f"{day}T{hour:02d}:00:00.000", "ad_spend": "10"})
    return rows


@pytest.mark.asyncio
async def test_elapsed_only_counts_each_day_up_to_the_current_hour(at_noon) -> None:
    mcp = _Mcp(_hour_rows())
    deps = _deps(mcp=mcp)
    result = await semantic.query_metrics(
        _Ctx(deps),
        "ad_spend",
        period_start=datetime(2026, 10, 4, tzinfo=IST),
        period_end=datetime(2026, 10, 6, tzinfo=IST),
        elapsed_only=True,
    )
    assert result.success, result.summary
    assert mcp.calls[0]["granularity"] == "hour"
    # 3 days x 12 elapsed hours x 10 = 360; per day 120.
    assert "total=360.0" in result.summary and "per-day average 120.00 over 3 days" in result.summary
    payload = deps.artifact_store.get(result.artifact_ids[0]).payload
    assert payload["value"] == 360.0 and payload["dimensions"][elapsed.ELAPSED_KEY] == "12:00"


@pytest.mark.asyncio
async def test_elapsed_only_refuses_a_ratio(at_noon) -> None:
    result = await semantic.query_metrics(
        _Ctx(_deps(mcp=_Mcp([]))),
        "gross_roas",
        period_start=TODAY,
        period_end=TODAY,
        elapsed_only=True,
    )
    assert not result.success and "additive" in result.summary


@pytest.mark.asyncio
async def test_a_total_that_includes_today_carries_the_in_progress_note(at_noon) -> None:
    mcp = _Mcp([{"ad_spend": "26040.83"}])
    result = await semantic.query_metrics(_Ctx(_deps(mcp=mcp)), "ad_spend", period_start=TODAY, period_end=TODAY)
    assert result.success and "still in progress" in result.summary


# -- model chain ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_malformed_completion_falls_through_to_the_next_model(monkeypatch) -> None:
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    async def malformed(self, *args, **kwargs):
        raise UnexpectedModelBehavior("Invalid response from openai chat completions endpoint")

    monkeypatch.setattr(OpenAIChatModel, "request", malformed)
    health = ModelHealth()
    gated = HealthGatedChatModel("tail", provider=OpenAIProvider(api_key="x"), health=health, health_key="tail")
    health.register("other")
    with pytest.raises(ModelAPIError, match="malformed response"):
        await gated.request([], None, None)
    assert health.should_skip("tail")
    chain = FallbackModel(
        HealthGatedChatModel("bad", provider=OpenAIProvider(api_key="x"), health=ModelHealth(), health_key="bad"),
        FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("ok")])),
    )
    run = await Agent(PatientModel(chain, health=ModelHealth())).run("q")
    assert run.output == "ok"
    assert model_health.AGENT_LLM_WAIT_BUDGET_S >= 0


class _HourAwareMcp(_Mcp):
    """Daily total for a plain query, hourly rows when asked for granularity=hour."""

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.calls.append(arguments)
        rows = _hour_rows() if arguments.get("granularity") == "hour" else [{"ad_spend": "720"}]
        return {"rows": rows, "provenance": {"query_id": "q1", "currency": "INR"}}


def _comparing_today(deps: SelericDeps) -> SelericDeps:
    import dataclasses

    from seleric_swarm.agent.scope import RequiredScope, RequiredWindow

    windows = (
        RequiredWindow(TODAY.date().replace(day=4), TODAY.date().replace(day=6)),
        RequiredWindow(TODAY.date(), TODAY.date()),
    )
    return dataclasses.replace(deps, required_scope=RequiredScope(windows=windows))


@pytest.mark.asyncio
async def test_a_reference_window_compared_with_today_also_gets_the_same_hours(at_noon) -> None:
    deps = _comparing_today(_deps(mcp=_HourAwareMcp([])))
    result = await semantic.query_metrics(
        _Ctx(deps),
        "ad_spend",
        period_start=datetime(2026, 10, 4, tzinfo=IST),
        period_end=datetime(2026, 10, 6, tzinfo=IST),
    )
    assert result.success
    assert "ad_spend=720.0 over 2026-10-04..2026-10-06" in result.summary
    assert "SAME HOURS AS TODAY" in result.summary and "per-day average 120.00 over 3 days" in result.summary
    assert len(result.artifact_ids) == 2


@pytest.mark.asyncio
async def test_a_ranking_keeps_its_entities_and_gets_the_note_instead(at_noon) -> None:
    deps = _comparing_today(_deps(mcp=_HourAwareMcp([])))
    result = await semantic.query_metrics(
        _Ctx(deps),
        "ad_spend",
        period_start=datetime(2026, 10, 4, tzinfo=IST),
        period_end=datetime(2026, 10, 6, tzinfo=IST),
        order="desc",
        limit=5,
    )
    assert result.success and "SAME HOURS AS TODAY" not in result.summary
    assert "query this window with elapsed_only=True as well" in result.summary


@pytest.mark.asyncio
async def test_elapsed_only_names_what_a_finer_breakdown_leaves_out(at_noon) -> None:
    rows = [
        {"report_hour.hour": "2026-10-07T09:00:00.000", "ad_name": "A", "ad_spend": "10"},
        {"report_hour.hour": "2026-10-07T09:00:00.000", "ad_name": None, "ad_spend": "25"},
    ]
    result = await semantic.query_metrics(
        _Ctx(_deps(mcp=_Mcp(rows))),
        "ad_spend",
        dimensions={"ad_name": ""},
        period_start=TODAY,
        period_end=TODAY,
        elapsed_only=True,
    )
    assert result.success
    assert "ad_name=A=10.0" in result.summary
    assert "25 of the period's ad_spend has no ad_name value" in result.summary


def test_a_derailed_limitation_is_sent_back() -> None:
    result = MissionResult(
        mission_id="MS3-pm",
        status="completed",
        query="q",
        as_of=TODAY,
        final_response="Spend was 100.",
        limitations=["I'm going to redo properly. Apologies. (End) " * 40],
    )
    outcome = EvidenceValidator().validate(result, deps=_deps())
    assert not outcome.ok and "limitations entry" in (outcome.reason or "")


def test_a_comparison_question_carries_both_windows() -> None:
    from seleric_swarm.agent import runner
    from seleric_swarm.agent.scope import required_windows_from_resolved

    window = runner._question_window("last 3 days vs today", "Asia/Kolkata", "2026-10-07")
    assert [str(w) for w in required_windows_from_resolved(window)] == ["2026-10-04..2026-10-06", "2026-10-07"]


def test_a_bare_value_from_a_data_mission_is_sent_back() -> None:
    store = InMemoryArtifactStore()
    aid = _evidence(store, "ad_spend", TODAY, TODAY, 4431.03)
    result = MissionResult(
        mission_id="MS3-pm", status="completed", query="q", as_of=TODAY, final_response="4431.03", evidence_ids=[aid]
    )
    outcome = EvidenceValidator().validate(result, deps=_deps(store))
    assert not outcome.ok and "bare value" in (outcome.reason or "")


def test_figures_without_any_citation_are_sent_back() -> None:
    store = InMemoryArtifactStore()
    _evidence(store, "ad_spend", TODAY, TODAY, 4431.03)
    result = MissionResult(
        mission_id="MS3-pm",
        status="completed",
        query="q",
        as_of=TODAY,
        final_response="Ad spend today is INR 4,431.03 so far.",
    )
    outcome = EvidenceValidator().validate(result, deps=_deps(store))
    assert not outcome.ok and "cites no evidence_ids" in (outcome.reason or "")


# ---------------------------------------------------------------- 2026-10-07 night (golden set)
def test_digits_inside_an_entity_name_are_not_a_stated_total():
    from seleric_swarm.agent.validation.answer_audit import header_key, total_mismatch

    answer = (
        "Suspender ad sales total INR 11,896.34.\n\n"
        "| Campaign | Ad name | Sales (INR) |\n| --- | --- | ---: |\n"
        "| TH-383-SUSPENDER-29SEP | TH-383-SUSPENDER-UGC | 7,611.74 |\n"
        "| TH-445-PROSUSPENDERBOOTS-6OCT | TH-445-PROSUSPENDERBOOTS-UGC | 4,284.60 |\n\n"
        "The rows reconcile exactly to the total; TH-383-SUSPENDER-UGC provided ~64% of it."
    )
    labels = frozenset({header_key("Campaign"), header_key("Ad name")})
    assert total_mismatch(answer, labels) is None
    # A genuinely wrong total is still caught.
    assert total_mismatch(answer.replace("11,896.34", "12,896.34"), labels) is not None


def test_a_baseline_window_disjoint_from_the_named_period_is_not_pulled_back():
    """Live 2026-10-07 MS3-d13b253ea0: "compare to the last 7 days" pinned every call
    to 09-30..10-06, so the previous week could never be fetched."""
    from datetime import datetime
    from types import SimpleNamespace

    from seleric_swarm.contracts.lookup import TimeRangeV1
    from seleric_swarm.toolsets.semantic import _pin_to_resolved_window

    window = TimeRangeV1(kind="absolute", start="2026-09-30", end="2026-10-06", relative_token="last_7d")
    ctx = SimpleNamespace(deps=SimpleNamespace(resolved_window=window))

    def asked(a: str, b: str):
        return _pin_to_resolved_window(ctx, datetime.fromisoformat(a), datetime.fromisoformat(b))

    assert asked("2026-09-23", "2026-09-29") is None  # the previous 7 days: a baseline
    assert asked("2026-09-30", "2026-10-06") is None  # the named period itself
    drift = asked("2026-09-30", "2026-10-05")  # a miscomputed version of it
    assert drift is not None and drift[0].date().isoformat() == "2026-09-30"
    assert drift[1].date().isoformat() == "2026-10-06"


def test_a_signed_bridge_and_a_two_row_change_are_the_tables_own_arithmetic():
    """Live 2026-10-08 (Q15): "−18,586.67" read as unsigned, and the change between
    the event-day and previous-day rows was called a wrong total."""
    from seleric_swarm.agent.validation.answer_audit import total_mismatch

    bridge = (
        "Net profit fell by INR −18,585.67 in total.\n\n| Component | Change (INR) |\n| --- | ---: |\n"
        "| Gross sales | −50,768.00 |\n| Discounts | +951.00 |\n| Returns | +4,023.33 |\n"
        "| COGS | +16,745.00 |\n| Ad spend | +10,463.00 |\n"
    )
    assert total_mismatch(bridge) is None
    assert total_mismatch(bridge.replace("−18,585.67", "−19,585.67")) is not None
    rows = (
        "The total change in net profit was INR −18,586.67.\n\n| Date | Net profit (INR) |\n| --- | ---: |\n"
        "| 2026-10-02 | -11,641.95 |\n| 2026-10-01 | 6,944.72 |\n"
    )
    assert total_mismatch(rows) is None
    assert total_mismatch(rows.replace("18,586.67", "19,586.67")) is not None


def test_a_period_comparison_that_names_no_measure_is_not_planned():
    """Golden Q6 (2026-10-08): an empty-metric plan made the agent ask which metrics."""
    from datetime import date

    from seleric_swarm.agent.plan import PlanSlots, compose_plan
    from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot

    slots = PlanSlots(shape="period_comparison")
    windows = [(date(2026, 10, 1), date(2026, 10, 7)), (date(2026, 9, 1), date(2026, 9, 7))]
    plan, notes = compose_plan(
        slots, metric_ids=[], rank_id=None, windows=windows, catalogue=CatalogueSnapshot(), as_of=None
    )
    assert plan is None and "no usable step" in notes


def test_a_pipe_inside_a_cell_does_not_shift_the_columns():
    """Golden Q16 (2026-10-08): "[Google Build] Brand Search | 5th March" split a row,
    so a correct total was rejected twice."""
    from seleric_swarm.agent.validation.answer_audit import table_cells, total_mismatch

    assert table_cells(r"| Brand Search \| 5th March | -3,558.22 |") == ["Brand Search | 5th March", "-3,558.22"]
    escaped = (
        "The three campaigns lost INR -9,767.0 in total.\n\n| Campaign | Net profit |\n| --- | ---: |\n"
        "| Brand Search \\| 5th March | -3,558.22 |\n| TH-383-SUSPENDER-26SEP-ADV+ | -3,122.10 |\n"
        "| Demand Gen \\| Hero Products | -3,086.68 |\n"
    )
    assert total_mismatch(escaped, frozenset({"campaign"})) is None
    assert total_mismatch(escaped.replace("9,767.0", "9,867.0"), frozenset({"campaign"})) is not None


def test_a_rollup_of_fetched_rows_along_their_own_dimension_is_backed():
    """Golden Q17 (2026-10-08): per-channel and total sums of rows fetched at
    channel × sub-channel grain were rejected as unbacked until the mission failed."""
    from datetime import UTC, datetime

    from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
    from seleric_swarm.agent.validation import _mission_values
    from seleric_swarm.conversations.contracts import ContextBundle, Principal
    from seleric_swarm.conversations.contracts import Artifact
    from seleric_swarm.state.artifacts import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    deps = SelericDeps(
        mission_id="m1", as_of=datetime(2026, 10, 8, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t", run_id="r", trace_id="tr", context=ContextBundle(), mcp_client=NullMcpClient(),
        artifact_store=store, limits=ExecutionLimits(),
    )
    rows = [("meta", "ig_feed", 261300.64), ("meta", "fb_feed", 96145.28), ("google", "pmax", 169356.01)]
    for channel, sub, value in rows:
        store.put(Artifact(
            workspace_id="w", artifact_type="evidence", classification="factual", mission_id="m1",
            evidence_ids=[f"q:{channel}:{sub}"],
            payload={"metric_id": "product_net_revenue", "dimensions": {"finance_channel": channel, "sub_channel": sub},
                     "grain": "none", "period_start": "2026-08-09", "period_end": "2026-10-07", "value": value},
        ))
    values = _mission_values(deps)
    assert any(abs(v - 357445.92) < 0.01 for v in values)  # Meta = its two rows
    assert any(abs(v - 526801.93) < 0.01 for v in values)  # every row of the query
    assert not any(abs(v - 430656.65) < 0.01 for v in values)  # ig_feed + pmax: no shared dimension value

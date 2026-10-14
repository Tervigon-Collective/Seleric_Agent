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




















# ---------------------------------------------------------------- 2026-10-07 night (golden set)













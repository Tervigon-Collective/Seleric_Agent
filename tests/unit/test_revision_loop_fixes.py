"""Regression tests for the 2026-10-04 revision-loop failure (MS3-34e7eb26aa).

"give me daily chart of ad spend for the last 30 days": the data was right on
the first fetch, yet the mission streamed seven drafts, made 25 tool calls and
failed INSUFFICIENT_EVIDENCE, shipping a draft whose total contradicted its own
table. Each test pins one of the defects found in that post-mortem.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.output import MissionResult, ToolResult
from seleric_swarm.agent.progress import _tool_problem
from seleric_swarm.agent.validation import EvidenceValidator, run_validated_mission
from seleric_swarm.agent.validation.answer_audit import total_mismatch
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import analytics, semantic

MISSION = "MS3-test"

# The 27 daily values the live mission fetched (true total 1,086,296.24).
DAILY = [
    78943.28, 69697.74, 59576.86, 66653.46, 66399.25, 68506.52, 62669.59, 75210.80,
    60866.95, 9274.29, 0.00, 0.00, 0.00, 0.00, 0.00, 15665.96, 26636.72, 44679.10,
    44991.74, 38071.69, 40800.91, 46669.43, 36055.94, 45967.59, 35504.53, 54601.16,
    38852.73,
]


def _table() -> str:
    rows = "\n".join(f"| 2026-09-{i + 1:02d} | {v:,.2f} |" for i, v in enumerate(DAILY))
    return f"| Date | Spend |\n| --- | ---: |\n{rows}\n"


def _deps(store: InMemoryArtifactStore | None = None, *, revisions: int = 1) -> SelericDeps:
    return SelericDeps(
        mission_id=MISSION,
        as_of=datetime.now(UTC),
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
        mcp_client=NullMcpClient(),
        artifact_store=store or InMemoryArtifactStore(),
        limits=ExecutionLimits(max_validation_revisions=revisions),
    )


def _seed(store: InMemoryArtifactStore, values: list[float] = DAILY) -> list[str]:
    ids = []
    for i, value in enumerate(values):
        day = datetime(2026, 9, i + 1, tzinfo=UTC)
        payload = EvidenceArtifact(
            metric_id="metric.some_spend",
            grain="day",
            as_of=datetime.now(UTC),
            period_start=day,
            period_end=day,
            value=value,
            source_query={"q": i},
        ).model_dump(mode="json")
        ids.append(
            store.put(
                Artifact(
                    workspace_id="ws-1",
                    artifact_type="evidence",
                    payload=payload,
                    classification="factual",
                    evidence_ids=[f"raw:{i}"],
                    provenance=ArtifactProvenance(evidence_ids=[f"raw:{i}"], query_version="q1"),
                    mission_id=MISSION,
                )
            ).id
        )
    return ids


# -- total audit ---------------------------------------------------------------


def test_peak_value_beside_a_no_total_sentence_is_not_a_total_claim() -> None:
    # Draft 5 verbatim shape: one paragraph, a peak value, then "No total is reported".
    text = _table() + (
        "\nInterpretation: The largest single-day spend in the period was 78,943.28 INR on "
        "2026-09-05. No total is reported to avoid the prior mismatch between table sum and "
        "stated total; please let me know if you want the exact summed total.\n"
    )
    assert total_mismatch(text) is None


@pytest.mark.parametrize("wrong", ["1,072,316", "1,292,917", "1,252,927"])
def test_wrong_totals_on_a_long_table_are_still_caught(wrong: str) -> None:
    assert total_mismatch(f"Total spend was INR {wrong}.\n\n" + _table()) is not None


@pytest.mark.parametrize(
    "claim",
    [
        f"{sum(DAILY):,.2f}",  # the whole column
        f"{sum(DAILY[-3:]):,.2f}",  # a consecutive run ("last 3 days")
        f"{sum(DAILY[:-1]):,.2f}",  # all but one row ("excluding today")
    ],
)
def test_legitimate_totals_on_a_long_table_reconcile(claim: str) -> None:
    assert total_mismatch(f"Spend totalled {claim}.\n\n" + _table()) is None


# -- zero-tool gate ------------------------------------------------------------


def test_answer_from_already_fetched_evidence_is_not_a_preamble() -> None:
    store = InMemoryArtifactStore()
    _seed(store)
    result = MissionResult(
        status="completed",
        final_response="Daily spend is in the table." + "\n\n" + _table(),
        trace={"steps": [{"kind": "tool_call", "tool": "final_result"}]},
    )
    outcome = EvidenceValidator().validate(result, deps=_deps(store))
    assert "before any other tool" not in (outcome.reason or "")


def test_preamble_with_no_mission_evidence_is_still_rejected() -> None:
    result = MissionResult(
        status="completed",
        final_response="Let me fetch the data now.",
        trace={"steps": [{"kind": "tool_call", "tool": "final_result"}]},
    )
    outcome = EvidenceValidator().validate(result, deps=_deps())
    assert not outcome.ok
    assert "before any other tool" in (outcome.reason or "")


# -- revision loop -------------------------------------------------------------


def _final(text: str, evidence_ids: list[str] | None = None) -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(
                "final_result",
                {"status": "completed", "final_response": text, "evidence_ids": evidence_ids or []},
            )
        ]
    )


def _user_prompts(messages: list) -> int:
    return sum(1 for m in messages for p in getattr(m, "parts", []) if p.part_kind == "user-prompt")


@pytest.mark.asyncio
async def test_revision_continues_the_conversation() -> None:
    fetches = 0

    def model(messages: list, _info: AgentInfo) -> ModelResponse:
        returns = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "fetch"
        ]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("fetch", {})])
        if _user_prompts(messages) == 1:
            return _final("")  # rejected: empty answer
        return _final("the revised answer is complete")

    agent = Agent(FunctionModel(model), deps_type=SelericDeps, output_type=MissionResult)

    @agent.tool_plain
    def fetch() -> str:
        nonlocal fetches
        fetches += 1
        return "rows"

    result = await run_validated_mission(agent, _deps(), "q")
    assert result.status == "completed"
    assert result.final_response == "the revised answer is complete"
    # The revision saw the first run's tool result instead of starting blind.
    assert fetches == 1
    assert result.trace["validation"]["revisions"][0]["reason"]


@pytest.mark.asyncio
async def test_a_miscopied_citation_is_repaired_without_a_revision() -> None:
    """Live 2026-10-08: a cited id that resolves to nothing cost a full revision (15
    runs in a week). An id with no close mission artifact is dropped, and an answer
    left citing nothing cites the mission's own evidence."""
    store = InMemoryArtifactStore()
    _seed(store)
    good = "Spend totalled " + f"{sum(DAILY):,.2f}.\n\n" + _table()
    calls = 0

    def model(_m: list, _i: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return _final(good, ["artifact_does_not_exist"])

    agent = Agent(FunctionModel(model), deps_type=SelericDeps, output_type=MissionResult)
    deps = _deps(store)
    result = await run_validated_mission(agent, deps, "q")

    assert result.status == "completed"
    assert calls == 1
    assert "artifact_does_not_exist" not in result.evidence_ids
    assert set(result.evidence_ids) == {a.id for a in store.list_for_mission(deps.mission_id) if a.artifact_type == "evidence"}


@pytest.mark.asyncio
async def test_a_stalled_revision_ships_the_draft_without_its_wrong_total() -> None:
    """The same figures rejected for the same reason twice: the loop stops instead of
    spending more revisions, and ships the table without the mis-added total rather
    than failing the mission (live 2026-10-08 MS3-28737df764)."""
    store = InMemoryArtifactStore()
    _seed(store)
    calls = 0

    def model(_m: list, _i: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return _final("Spend totalled 1,252,927.\n\n" + _table())

    agent = Agent(FunctionModel(model), deps_type=SelericDeps, output_type=MissionResult)
    result = await run_validated_mission(agent, _deps(store), "q")
    assert calls == 2
    assert result.status == "partial"
    assert "1,252,927" not in result.final_response
    assert _table().strip().splitlines()[0] in result.final_response
    assert "VALIDATION_STALLED" in result.limitations
    assert any("did not reconcile" in item for item in result.limitations)


# -- progress ------------------------------------------------------------------


def test_refused_tool_is_reported_as_failed() -> None:
    refused = ToolReturnPart(
        "generate_visualization",
        ToolResult(success=False, summary="evidence not found in store: x", error_code="INSUFFICIENT_EVIDENCE"),
    )
    ok = ToolReturnPart("generate_visualization", ToolResult(success=True, summary="chart"))
    # _tool_problem returns (problem_text, is_retry): a refusal is a failure,
    # a ModelRetry is the model re-evaluating its own arguments.
    problem, is_retry = _tool_problem(refused)
    assert problem is not None and "evidence not found" in problem
    assert is_retry is False
    assert _tool_problem(ok) == (None, False)


# -- tool summaries, evidence and charts ------------------------------------------


def _ctx(store: InMemoryArtifactStore, *aggregations: tuple[str, str]) -> SimpleNamespace:
    catalogue = CatalogueSnapshot(
        metrics=tuple(CatalogueMetricMeta(id=mid, raw={"aggregation": agg}) for mid, agg in aggregations)
    )
    return SimpleNamespace(
        deps=SimpleNamespace(
            mission_id=MISSION,
            artifact_store=store,
            catalogue=catalogue,
            canonical_metric_id=lambda mid: mid,
            principal=SimpleNamespace(workspace_id="ws-1"),
        )
    )


def test_series_stats_follow_the_catalogue_aggregation() -> None:
    ctx = _ctx(InMemoryArtifactStore(), ("m.sum", "additive"), ("m.rate", "ratio"))
    additive = semantic._series_stats(ctx, "m.sum", DAILY)
    assert "total=1086296.24" in additive
    ratio = semantic._series_stats(ctx, "m.rate", [0.5, 1.5])
    assert "total" not in ratio and "cannot be summed" in ratio
    unknown = semantic._series_stats(ctx, "m.unknown", [1.0, 2.0])
    assert "total" not in unknown and "min=1" in unknown


def test_identical_evidence_is_written_once_per_mission() -> None:
    store = InMemoryArtifactStore()
    ctx = _ctx(store)
    day = datetime(2026, 9, 1, tzinfo=UTC)

    def row(fetched: datetime) -> EvidenceArtifact:
        return EvidenceArtifact(
            metric_id="m", grain="day", as_of=fetched, period_start=day, period_end=day,
            value=1.5, source_query={"q": 1}, fetched_at=fetched,
        )

    prov = ArtifactProvenance(query_version="q1")
    first = semantic._put_evidence(ctx, row(datetime.now(UTC)), index=semantic._evidence_index(ctx), raw_id="r", provenance=prov)
    # A retry with fresh deps: new index, later fetch time, same fact.
    again = semantic._put_evidence(ctx, row(datetime.now(UTC)), index=semantic._evidence_index(ctx), raw_id="r", provenance=prov)
    assert first == again
    assert len(store.list_for_mission(MISSION)) == 1


def test_unknown_evidence_ids_refusal_lists_what_exists() -> None:
    store = InMemoryArtifactStore()
    ids = _seed(store, [1.0, 2.0])
    _, _, refusal = analytics._load_evidence(_ctx(store), ["artifact_guess"])
    assert refusal is not None and not refusal.success
    assert all(aid in refusal.summary for aid in ids)
    # A wrong id is a corrected-argument fix, not a dead end: INSTRUCTIONS tells
    # the model a non-retryable error does not justify re-calling, so marking this
    # one non-retryable is what made a single mistyped id abandon a mission
    # (live MS3-167d9f4838).
    assert refusal.retryable is True


def test_mistyped_evidence_id_is_repaired_when_unambiguous() -> None:
    """A dropped character in a 40-char artifact id is recoverable by construction.

    Live 2026-10-06 (MS3-167d9f4838): 47 ids were hand-copied into run_python,
    one lost its trailing character, and the whole computation was refused.
    """
    store = InMemoryArtifactStore()
    ids = _seed(store, [1.0, 2.0, 3.0])
    real = ids[1]
    evidence, resolved, refusal = analytics._load_evidence(_ctx(store), [*ids[:1], real[:-1], *ids[2:]])
    assert refusal is None
    assert resolved == [ids[0], real, ids[2]], "order must be preserved for the zip() callers"
    assert [e.metric_id for e in evidence] == [e.metric_id for e in evidence]
    assert resolved[1] in ids


def test_ambiguous_evidence_id_prefix_is_not_repaired() -> None:
    """Two candidates for one fragment resolve to nothing — never a guess.

    Guessing between them is how a finding ends up citing evidence that did not
    back it (non-negotiable rule 6). Real 32-hex ids never collide on an 8-char
    prefix, so the collision is staged here: a store handing back two ids that
    share one must still refuse.
    """
    store = InMemoryArtifactStore()
    seeded = _seed(store, [1.0, 2.0])
    shared = "artifact_abcdef01"
    collision = [
        stored.model_copy(update={"id": f"{shared}{suffix}"})
        for stored, suffix in zip(store.list_for_mission(MISSION), ("0", "1"), strict=True)
    ]
    assert all(a.id.startswith(shared) for a in collision)
    store.list_for_mission = lambda mission_id: collision  # type: ignore[method-assign]

    _, resolved, refusal = analytics._load_evidence(_ctx(store), [f"{shared}0"])
    assert refusal is not None and not refusal.success
    assert resolved == []
    assert seeded, "seed must not be empty for this test to mean anything"


@pytest.mark.asyncio
async def test_same_chart_is_one_artifact() -> None:
    store = InMemoryArtifactStore()
    ids = _seed(store, DAILY[:5])
    ctx = _ctx(store)
    first = await analytics.generate_visualization(ctx, ids, "trend", "Spend")
    again = await analytics.generate_visualization(ctx, ids, "trend", "Spend")
    assert first.success and first.artifact_ids == again.artifact_ids
    assert sum(a.artifact_type == "chart_spec" for a in store.list_for_mission(MISSION)) == 1

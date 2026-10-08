"""Content guard for ``agent/instructions.py``.

Live incident (2026-09-21): a "top-selling product" mission had no guidance
on how to find a valid dimension name for a non-time breakdown, so the model
guessed dimension key spellings one tool call at a time until it hit
pydantic_ai's ``tool_calls_limit`` (``UsageLimits`` in
``agent/validation/__init__.py``) without ever answering. ``get_metric_definition``
was already a registered tool returning ``supported_dimensions`` — the fix
is instructing the model to use it, not adding a new tool.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seleric_swarm.agent.instructions import INSTRUCTIONS


def test_non_time_breakdowns_are_told_to_check_supported_dimensions_first():
    assert "get_metric_definition" in INSTRUCTIONS
    assert "supported_dimensions" in INSTRUCTIONS
    assert "do not guess the" in INSTRUCTIONS.lower()


def test_agent_is_told_not_to_ask_permission_to_continue_a_lookup():
    """Live incident (2026-09-21): a plain "net sales last month" lookup
    called search_semantics, got the metric id, then ended the mission
    asking the user "want me to pull that number now?" instead of calling
    query_metrics itself — status "completed" with zero evidence. Rule 3
    ("you decide the next tool call yourself") already existed but wasn't
    blunt enough to override the model's instinct to ask permission."""
    lowered = INSTRUCTIONS.lower()
    assert "never end your turn to ask the user" in lowered
    assert "want me to pull the number now" in lowered


def test_agent_is_told_to_switch_metrics_for_entity_level_breakdowns_it_cant_support():
    """Live spot-check (2026-09-21): a metric on a summary-level view (only
    brand/date dimensions) can't answer "which products/ads drove this" no
    matter how it's queried, and nothing in the catalogue automatically
    points to the sibling metric that does support that breakdown. The model
    must know to re-search the catalogue for a sibling metric that carries
    the needed dimension instead of forcing a drilldown that will fail or
    silently answering with the wrong metric's number. Phrased generically
    (no hardcoded metric/dimension names) so it generalizes across catalogue
    changes instead of only matching the specific metrics from the incident."""
    lowered = INSTRUCTIONS.lower()
    assert "search the catalogue again" in lowered
    assert "supported_dimensions" in lowered
    assert "not necessarily identical" in lowered


def test_agent_labels_per_unit_figures_by_their_denominator():
    """Stress-test L9 (P3): a per-order average was labeled "LTV per customer".
    The label must match the denominator — orders are not customers."""
    lowered = INSTRUCTIONS.lower()
    assert "per order" in lowered and "per customer" in lowered
    assert "do not relabel orders as customers" in lowered


def test_agent_ranks_profitability_by_net_profit_not_contribution_margin():
    """Stress-test L8 (P3): Meta was called the "best" channel on contribution
    margin while it had the most-negative net profit. CM is pre-advertising, so
    it must not decide "best", and it must be labeled as pre-advertising."""
    collapsed = " ".join(INSTRUCTIONS.lower().split())
    assert "pre-advertising" in collapsed
    assert "rank by net profit, not contribution margin" in collapsed


def test_agent_ranks_superlative_comparisons_with_ordered_query_not_drilldown_python():
    """Live incident (2026-09-26): "compare the top product Aug'26 vs Aug'25"
    ran drilldown (one evidence row per product, ~180 rows/period) then tried
    run_python to pick the max. The model had to transcribe hundreds of opaque
    artifact ids into the sandbox, dropped a trailing char off several, and got
    INSUFFICIENT_EVIDENCE — a wasted round and most of the 72s. query_metrics
    already supports order+limit; a superlative-across-periods question is just
    a top-N query per period. Phrased generically (no product/metric/period
    names) so it generalizes."""
    lowered = INSTRUCTIONS.lower()
    assert "superlative entity is still a top-n query" in lowered
    assert "do not ``drilldown`` every row and then pick the max/min in" in lowered
    assert "never for a ranking or selection" in lowered


def test_agent_is_told_to_batch_independent_metric_fetches_in_one_turn():
    """Live incident (2026-09-21): a question needing three independent
    metrics (no metric derived from another) fetched them one per turn,
    ~30-40s apart each due to slow live queries, and blew the mission's 120s
    wall-clock budget after only 3 tool calls. query_metrics's metric_id
    parameter is a frozen signature (CONTRACTS.md) — it cannot take a list,
    so the fix is getting the model to issue independent query_metrics calls
    together in one turn (which run in parallel) instead of spread across
    turns (which run serially)."""
    lowered = INSTRUCTIONS.lower()
    assert "issue those" in lowered
    assert "run in" in lowered and "parallel" in lowered


def test_response_format_contract_is_present():
    """Regression (2026-09-28): commit 9712837 rewrote INSTRUCTIONS from 262 to
    157 lines and deleted the whole USER-FACING RESPONSE FORMAT block, so
    answers stopped rendering tables and started leaking internal fields."""
    assert "USER-FACING RESPONSE FORMAT" in INSTRUCTIONS
    lowered = INSTRUCTIONS.lower()
    assert "lead with the answer" in lowered
    assert "markdown table" in lowered
    assert "no data available" in lowered


def test_series_must_render_as_a_table_not_label_value_lines():
    """Live sample: a 7-day series shipped as bare "date: value" lines instead
    of a table, which is unreadable in chat."""
    lowered = INSTRUCTIONS.lower()
    assert 'never emit a bare' in lowered
    assert '"label: value" lines for a series' in lowered


def test_answer_must_interpret_not_just_list_numbers():
    lowered = INSTRUCTIONS.lower()
    assert "say what the numbers mean, not only what they are" in lowered
    assert "flag it as needing verification" in lowered


def test_figures_must_be_internally_consistent_in_one_scale():
    """Live sample: the lead sentence carried a magnitude ten times smaller
    than the sum of the table printed directly beneath it."""
    lowered = INSTRUCTIONS.lower()
    assert "keep every figure in one scale and one unit" in lowered
    assert "a total must equal the sum of the rows you printed" in lowered


def test_internal_field_dump_is_forbidden_in_the_answer():
    """Live sample: the answer ended with literal "Evidence IDs:" and
    "Limitations:" blocks exposing artifact ids and internal metric names."""
    lowered = INSTRUCTIONS.lower()
    assert "do not append a trailing section of internal fields" in lowered
    assert '"evidence ids"' in lowered
    assert '"coverage"' in lowered
    assert "never narrate how you obtained the number" in lowered


def test_incomplete_periods_must_be_marked_and_not_trended_as_growth():
    """Live sample: a 3-month total summed two partial months with two full
    ones, and the rising month-over-month series ended on a still-running
    month presented as if it were complete."""
    lowered = INSTRUCTIONS.lower()
    assert "an incomplete period is not comparable to a complete one" in lowered
    assert "mark it in the" in lowered and "row label itself" in lowered
    assert "do not present a total that mixes partial and full periods" in lowered


def test_output_contract_is_anchored_at_the_end_of_the_prompt():
    """Live 2026-09-29: the full USER-FACING RESPONSE FORMAT block sat ~7k
    characters from the end of the composed prompt, behind the capability
    manifest, and the fast model tier ignored it — shipping bullet lists,
    "Scope:" lines and trailing "Evidence IDs"/"Notes" blocks. The compressed
    contract is registered last so it lands next to generation."""
    from seleric_swarm.agent.instructions import OUTPUT_CONTRACT

    lowered = OUTPUT_CONTRACT.lower()
    assert "markdown table" in lowered
    assert "delimiter row" in lowered
    assert "nothing" in lowered and "may follow them" in lowered
    for banned in ("scope", "notes", "coverage", "limitations", "evidence\n  ids"):
        assert banned in lowered, banned


def test_output_contract_is_registered_after_every_other_instruction():
    """Position is the whole point — a new @agent.instructions registered after
    it would push the contract away from the end again."""
    import inspect

    from seleric_swarm.agent import agent as agent_mod

    source = inspect.getsource(agent_mod.build_seleric_agent)
    decorated = [
        line for line in source.splitlines() if line.strip().startswith("def _")
    ]
    assert decorated, "expected at least one dynamic instructions function"
    assert "_output_contract" in decorated[-1], (
        f"_output_contract must be registered last, found: {decorated}"
    )


def test_only_one_answer_format_authority_exists():
    """Live 2026-09-29: section 6 carried a second, weaker format spec ("keep
    simple lookups to a short answer") that licensed the bullet lists the
    response-format contract forbids. Two models obeyed the weaker one."""
    lowered = INSTRUCTIONS.lower()
    assert "keep simple lookups to a short answer" not in lowered
    assert "governed solely by user-facing response format" in lowered


def test_limitations_and_evidence_ids_are_named_as_structured_fields():
    """Live 2026-09-29: "Populate limitations..." read as an instruction to
    write a Limitations section into the answer text."""
    lowered = INSTRUCTIONS.lower()
    assert "these are\n  tool arguments, not text" in lowered
    assert "never restate either one inside final_response" in lowered


# --- dynamic instructions: the tool-availability announcement ---------------
# Live 2026-09-30 (thread_14d713b4): conversation missions withdraw every tool
# via PrepareTools, but nothing told the model — it kept following the "query
# the metric first" instructions with nothing to call and narrated its work
# through final_result(status="running") until revisions exhausted.


def _system_prompt_text(messages) -> str:
    from pydantic_ai.messages import ModelRequest

    # pydantic-ai renders @agent.instructions (static + dynamic) into
    # ModelRequest.instructions, not into SystemPromptPart parts.
    return "\n".join(m.instructions or "" for m in messages if isinstance(m, ModelRequest))


async def _prompt_for_run(call_counts: dict) -> str:
    """Run the agent once on a FunctionModel and return its system prompt."""
    import contextlib

    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from seleric_swarm.agent.agent import build_seleric_agent
    from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
    from seleric_swarm.conversations.contracts import ContextBundle, Principal
    from seleric_swarm.state.artifacts import InMemoryArtifactStore

    prompts: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompts.append(_system_prompt_text(messages))
        return ModelResponse(parts=[TextPart("You're welcome!")])

    deps = SelericDeps(
        mission_id="MS3-instr",
        as_of=datetime(2026, 9, 30, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t",
        run_id="r",
        trace_id="tr",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
        call_counts=call_counts,
    )
    # The plain-text reply is not a valid MissionResult; only the rendered
    # system prompt is under test here.
    with contextlib.suppress(Exception):
        await build_seleric_agent(model=FunctionModel(model)).run("thanks!", deps=deps)
    assert prompts, "the model was never asked for a response"
    return prompts[0]


@pytest.mark.asyncio
async def test_conversational_turn_is_told_it_has_no_tools() -> None:
    from seleric_swarm.agent.agent import CONVERSATIONAL

    prompt = await _prompt_for_run({CONVERSATIONAL: 1})
    lowered = prompt.lower()
    assert "no tools are available this turn" in lowered
    assert "never 'running'" in lowered
    assert "do not claim you ran or will run" in lowered


@pytest.mark.asyncio
async def test_normal_turn_is_not_told_that_tools_are_missing() -> None:
    prompt = await _prompt_for_run({})
    assert "NO TOOLS ARE AVAILABLE THIS TURN" not in prompt


def test_agent_is_told_to_merge_companion_breakdowns_before_answering():
    """Live 2026-10-08 (e4ad507): three independent top-10 tables for spend /
    sessions / orders — user could not join them. Prefer analyze(method=merge)."""
    lowered = INSTRUCTIONS.lower()
    assert 'method="merge"' in lowered
    assert "never ship three independent top-n lists" in lowered
    assert "one table" in lowered


def test_agent_applies_scope_via_conformed_siblings_not_same_dim_key():
    """Live 2026-10-08: 'same dimension and value' forced finance_channel onto
    ad_spend and leaked Google campaigns into Meta answers."""
    lowered = INSTRUCTIONS.lower()
    assert "same dimension and value" not in lowered
    assert "ad_platform" in lowered and "finance_channel" in lowered
    assert "do not force one dim key" in lowered

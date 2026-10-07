"""The one ``SelericAgent = Agent[SelericDeps, MissionResult]``.

Sprint 4 (``docs/refactor/01_PROFILE_RUNTIME.md``/``SPRINT_PLAN.md``): **all
seven** frozen toolset surfaces are now registered — the five from Profile
B/C's Sprint 1-3 work plus ``knowledge`` and ``experiments``, which Profile C
added in Sprint 4's additive track. That covers the 23 functions frozen in
``CONTRACTS.md`` §4, plus later additive tools (the python sandbox, the batch
``get_metric_definitions``, ``resolve_brand``, and the read-only ``ads``
surfaces, since withdrawn — see below) — 26 in all. ``tests/unit/test_v3_agent_wiring.py`` holds the count
and the name set so a future toolset cannot be written and then silently left
unregistered.

Model defaults to ``TestModel`` with ``call_tools=[]`` (deterministic, no
API key needed, and — now that real tools are registered — explicitly
**not** exercised, so the 0%-traffic stub path stays exactly as cheap and
side-effect-free as before this wiring landed). Pass a real ``model=`` (or
a scripted ``FunctionModel``/``TestModel(call_tools="all")`` for
integration testing) to actually exercise the tools.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import PrepareTools, ProcessHistory
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition

from seleric_swarm.agent.context import compact_history
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.instructions import INSTRUCTIONS, OUTPUT_CONTRACT
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.toolsets import (
    actions,
    analytics,
    causal,
    diagnosis,
    experiments,
    knowledge,
    models,
    sandbox,
    semantic,
)
from seleric_swarm.agent.limits import withdrawn_tools
from seleric_swarm.agent.repeat_guard import RepeatCallGuard
from seleric_swarm.toolsets.semantic import LIVE_DATA_UNAVAILABLE

# Typed as `list[Any]` deliberately: pydantic_ai's own `tools` parameter
# wants a `Sequence[Tool[SelericDeps] | ToolFuncEither[SelericDeps, ...]]`,
# and mypy cannot unify tool functions with genuinely different
# parameter lists into that single Callable shape even though every one of
# them is a real `RunContext[SelericDeps]`-first tool function (verified at
# runtime — see tests/unit/test_v3_agent_wiring.py, which registers and
# introspects all 26). Narrowing the annotation here is honest about a
# real typing-system limitation, not a suppression of a real bug.
TOOLS: list[Any] = [
    # Metric discovery: one call resolves every measure the question names
    # (concept resolver, glossary search fallback, or the full listing).
    semantic.find_metrics,
    semantic.resolve_brand,
    semantic.get_metric_definitions,
    semantic.query_metrics,
    semantic.semantic_sql,
    semantic.drilldown,
    # toolsets/ads.py (Meta/Google platform APIs) is deliberately not
    # registered: those are third-party surfaces outside the certified Cube
    # serve views, and the gateway no longer exposes them by default. Meta ad
    # delivery numbers stay reachable through query_metrics (meta_ad_performance).
    # The six calculations over fetched evidence share one tool (method=...).
    analytics.analyze,
    analytics.generate_visualization,
    sandbox.run_python,
    diagnosis.diagnose_metric_change,
    # estimate_effect runs its refuters itself; refute_estimate only re-ran them.
    causal.estimate_effect,
    models.forecast,
    # propose_action validates and previews; validate/preview only re-read it.
    actions.propose_action,
    actions.commit_action,
    knowledge.search_knowledge,
    experiments.get_experiment_history,
    experiments.estimate_sample_size,
    experiments.evaluate_experiment,
]
# predict_ltv / predict_propensity are not registered: no approved model exists
# (config/model_registry.yaml), so they could only refuse — and LTV as a metric
# is reachable through query_metrics (unit_economics).


def unbacked_tools(*, allow_writes: bool) -> frozenset[str]:
    """Registered tools with nothing behind them in this deployment, read from the
    same registries the tools themselves consult — so the model is not offered a
    tool that can only refuse. Adding an approved forecast model, an experiment, a
    knowledge document or enabling writes brings the tool back with no code change."""
    hidden: set[str] = set()
    if not allow_writes:
        hidden |= {"propose_action", "commit_action"}
    try:
        registry = models._registry()
        approved = {
            rec.model_type for rec in (registry.get(i) for i in registry.ids()) if rec and rec.status == "approved"
        }
        if "forecast" not in approved:
            hidden.add("forecast")
    except Exception:  # noqa: S110 - availability is advisory; the tool still refuses on its own
        pass
    try:
        if len(experiments._registry()) == 0:
            hidden |= {"get_experiment_history", "evaluate_experiment"}
    except Exception:  # noqa: S110
        pass
    try:
        from seleric_swarm.knowledge.corpus import load_corpus

        if not load_corpus():
            hidden.add("search_knowledge")
    except Exception:  # noqa: S110
        pass
    return frozenset(hidden)


def capability_manifest(tools: list[Any] | None = None) -> str:
    """One line per registered tool (name + first docstring line).

    Derived from the ``TOOLS`` list so a newly-registered tool shows up here
    automatically — the model gets an at-a-glance capability map to plan
    against instead of discovering tools one schema at a time.
    """
    lines = ["Tools available to you (call by name):"]
    for fn in TOOLS if tools is None else tools:
        name = getattr(fn, "__name__", str(fn))
        doc = (getattr(fn, "__doc__", "") or "").strip()
        summary = doc.splitlines()[0].strip() if doc else ""
        lines.append(f"- {name}: {summary}")
    return "\n".join(lines)


def registered_tool_names() -> frozenset[str]:
    """Names of every registered tool — what a plan step may name."""
    return frozenset(getattr(fn, "__name__", str(fn)) for fn in TOOLS)


def _stub_test_model() -> TestModel:
    # TestModel's default arbitrary-data generator doesn't respect datetime
    # field constraints (produces "a" for `as_of`, failing validation) — a
    # fixed custom output is what makes a *stub* agent, not a flaky one.
    # call_tools=[] additionally means none of the real tools registered
    # below get invoked against whatever (possibly fake) deps a 0%-traffic
    # caller supplies — tools ARE registered (Sprint 4), just not exercised
    # by this particular model.
    return TestModel(
        call_tools=[],
        custom_output_args={
            "mission_id": "stub",
            "status": "partial",
            "query": "",
            "as_of": datetime.now(UTC),
            "final_response": "Stub model — tools are registered but not exercised.",
            "evidence_ids": [],
            "finding_ids": [],
            "limitations": ["stub model — tools registered but not called"],
            "error_code": None,
            "trace": {},
        }
    )


CONVERSATIONAL = "conversational"
# Set on deps.call_counts when the planner's executor already fetched the plan's
# data (agent/executor.py): the agent then sees only the tools it may still need,
# not all of them (~6k tokens of schemas on every step).
PREFETCHED = "plan_prefetched"
_PREFETCHED_TOOLS = frozenset(
    {
        "query_metrics",
        "semantic_sql",
        "drilldown",
        "get_metric_definitions",
        "find_metrics",
        "analyze",
        "run_python",
        "generate_visualization",
    }
)

_STILL_AVAILABLE_WITHOUT_LIVE_DATA = frozenset(
    {
        "find_metrics",
        "get_metric_definitions",
        "search_knowledge",
    }
)


async def _withdraw_data_tools(
    ctx: RunContext[SelericDeps], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    """Once live data is known to be unreachable, only catalogue/knowledge tools
    remain — the model can no longer loop on fetches that cannot succeed. Any
    tool withdrawn during the mission (``limits.withdraw_tool``: a spent budget
    or a repeated call) is dropped, so the model moves on or answers instead of
    exhausting that tool's retries."""
    counts = getattr(ctx.deps, "call_counts", None)  # deps is None on the stub path
    if not counts:
        return tool_defs
    if counts.get(CONVERSATIONAL):
        return []
    if counts.get(LIVE_DATA_UNAVAILABLE):
        tool_defs = [t for t in tool_defs if t.name in _STILL_AVAILABLE_WITHOUT_LIVE_DATA]
    if counts.get(PREFETCHED):
        tool_defs = [t for t in tool_defs if t.name in _PREFETCHED_TOOLS]
    withdrawn = withdrawn_tools(ctx.deps)
    return [t for t in tool_defs if t.name not in withdrawn]


def build_seleric_agent(
    *, model: Model | str | None = None, hidden: frozenset[str] = frozenset()
) -> Agent[SelericDeps, MissionResult]:
    """Construct the agent with every implemented toolset registered, minus
    ``hidden`` (``unbacked_tools``: tools with nothing behind them here)."""
    tools = [fn for fn in TOOLS if getattr(fn, "__name__", "") not in hidden]
    agent = Agent(
        model=model or _stub_test_model(),
        deps_type=SelericDeps,
        output_type=MissionResult,
        instructions=INSTRUCTIONS + "\n\n" + capability_manifest(tools),
        name="seleric_agent",
        tools=tools,
        capabilities=[PrepareTools(_withdraw_data_tools), RepeatCallGuard(), ProcessHistory(compact_history)],
    )

    @agent.instructions
    def _working_memory(ctx: RunContext[SelericDeps]) -> str:
        # Zero-latency scratchpad read: the ledger rides the system prompt sent
        # each turn — no tool call, no round-trip. Null-safe for the deps=None
        # stub path (test_v3_agent_wiring's zero-tool run).
        pad = getattr(ctx.deps, "scratchpad", None)
        return pad.render() if pad is not None else ""

    @agent.instructions
    def _tool_availability(ctx: RunContext[SelericDeps]) -> str:
        # Live 2026-09-30 (thread_14d713b4, 2 failed runs): conversation-class
        # missions withdraw every tool via PrepareTools (_withdraw_data_tools),
        # but the model was never told — it kept following the "query the metric
        # first" instructions with nothing to call and used final_result as a
        # progress channel (status="running", "Running queries..."). The
        # validator then rejected it into re-running the same tool-less agent
        # until revisions exhausted (INSUFFICIENT_EVIDENCE). Announce the tool
        # state so the model answers instead of narrating work it cannot do.
        counts = getattr(ctx.deps, "call_counts", None)
        if not counts or not counts.get(CONVERSATIONAL):
            return ""
        return (
            "\n\n[tool status] NO TOOLS ARE AVAILABLE THIS TURN — this is a "
            "conversational turn, and final_result is the only call you can "
            "make. Therefore:\n"
            "- Answer the user's message directly in final_response: a real "
            "reply, never a plan, progress note, or promise of work.\n"
            "- Use a terminal status — 'completed' for a normal reply, "
            "'failed' only if the message cannot be answered at all. Never "
            "'running'.\n"
            "- You cannot fetch metrics here. Do not claim you ran or will run "
            "queries, do not restate prior figures as fresh results, and never "
            "invent numbers. If the message genuinely needs business data, say "
            "plainly what you would need and ask the user to send it as a data "
            "question."
        )

    @agent.instructions
    def _output_contract(ctx: RunContext[SelericDeps]) -> str:
        # Registered last so it lands at the very end of the composed prompt,
        # after the capability manifest and the scratchpad. Position is the
        # point: the same rules 7k characters earlier were being ignored.
        return OUTPUT_CONTRACT

    return agent

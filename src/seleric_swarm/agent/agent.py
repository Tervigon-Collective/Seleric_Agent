"""The one ``SelericAgent = Agent[SelericDeps, MissionResult]``.

Sprint 4 (``docs/refactor/01_PROFILE_RUNTIME.md``/``SPRINT_PLAN.md``): **all
seven** frozen toolset surfaces are now registered — the five from Profile
B/C's Sprint 1-3 work plus ``knowledge`` and ``experiments``, which Profile C
added in Sprint 4's additive track. That is all 23 functions frozen in
``CONTRACTS.md`` §4; ``tests/unit/test_v3_agent_wiring.py`` holds the count
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

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.instructions import INSTRUCTIONS
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.toolsets import (
    actions,
    analytics,
    causal,
    experiments,
    knowledge,
    models,
    sandbox,
    semantic,
)

# Typed as `list[Any]` deliberately: pydantic_ai's own `tools` parameter
# wants a `Sequence[Tool[SelericDeps] | ToolFuncEither[SelericDeps, ...]]`,
# and mypy cannot unify 15 tool functions with genuinely different
# parameter lists into that single Callable shape even though every one of
# them is a real `RunContext[SelericDeps]`-first tool function (verified at
# runtime — see tests/unit/test_v3_agent_wiring.py, which registers and
# introspects all 23). Narrowing the annotation here is honest about a
# real typing-system limitation, not a suppression of a real bug.
TOOLS: list[Any] = [
    semantic.search_semantics,
    semantic.get_metric_definition,
    semantic.query_metrics,
    semantic.drilldown,
    analytics.compare_periods,
    analytics.detect_anomalies,
    analytics.contribution_analysis,
    analytics.segment_decomposition,
    analytics.funnel_decomposition,
    analytics.cohort_analysis,
    sandbox.run_python,
    causal.estimate_effect,
    causal.refute_estimate,
    models.forecast,
    models.predict_ltv,
    models.predict_propensity,
    actions.propose_action,
    actions.validate,
    actions.preview,
    actions.commit_action,
    knowledge.search_knowledge,
    experiments.get_experiment_history,
    experiments.estimate_sample_size,
    experiments.evaluate_experiment,
]


def capability_manifest() -> str:
    """One line per registered tool (name + first docstring line).

    Derived from the ``TOOLS`` list so a newly-registered tool shows up here
    automatically — the model gets an at-a-glance capability map to plan
    against instead of discovering tools one schema at a time.
    """
    lines = ["Tools available to you (call by name):"]
    for fn in TOOLS:
        name = getattr(fn, "__name__", str(fn))
        doc = (getattr(fn, "__doc__", "") or "").strip()
        summary = doc.splitlines()[0].strip() if doc else ""
        lines.append(f"- {name}: {summary}")
    return "\n".join(lines)


def _stub_test_model() -> TestModel:
    # TestModel's default arbitrary-data generator doesn't respect datetime
    # field constraints (produces "a" for `as_of`, failing validation) — a
    # fixed custom output is what makes a *stub* agent, not a flaky one.
    # call_tools=[] additionally means none of the 23 real tools registered
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


def build_seleric_agent(*, model: Model | str | None = None) -> Agent[SelericDeps, MissionResult]:
    """Construct the agent with every implemented toolset registered."""
    return Agent(
        model=model or _stub_test_model(),
        deps_type=SelericDeps,
        output_type=MissionResult,
        instructions=INSTRUCTIONS + "\n\n" + capability_manifest(),
        name="seleric_agent",
        tools=TOOLS,
    )

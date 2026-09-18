"""The one ``SelericAgent = Agent[SelericDeps, MissionResult]``.

Sprint 1 scaffolding (``docs/refactor/01_PROFILE_RUNTIME.md``): no toolsets
are registered yet — that's Sprint 1-3 work for Profiles B/C
(``toolsets/semantic.py``, ``toolsets/analytics.py``, etc.). This module
exists so the loop's shape (one agent, one deps type, one output type) is
real code, not just a diagram, and so ``api/missions.py``'s stub endpoint
has something to call.

Model defaults to ``TestModel`` (deterministic, no API key needed) so this
runs in CI without live credentials — the point of a stub agent at 0%
traffic. Pass a real ``model=`` once a provider is wired for real use.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.instructions import INSTRUCTIONS
from seleric_swarm.agent.output import MissionResult


def _stub_test_model() -> TestModel:
    # TestModel's default arbitrary-data generator doesn't respect datetime
    # field constraints (produces "a" for `as_of`, failing validation) — a
    # fixed custom output is what makes a *stub* agent, not a flaky one.
    return TestModel(
        custom_output_args={
            "mission_id": "stub",
            "status": "partial",
            "query": "",
            "as_of": datetime.now(UTC),
            "final_response": "No toolsets are registered yet (Sprint 1 skeleton).",
            "evidence_ids": [],
            "finding_ids": [],
            "limitations": ["stub agent — no toolsets wired"],
            "error_code": None,
            "trace": {},
        }
    )


def build_seleric_agent(*, model: Model | str | None = None) -> Agent[SelericDeps, MissionResult]:
    """Construct the agent. No toolsets registered — see module docstring."""
    return Agent(
        model=model or _stub_test_model(),
        deps_type=SelericDeps,
        output_type=MissionResult,
        instructions=INSTRUCTIONS,
        name="seleric_agent",
    )

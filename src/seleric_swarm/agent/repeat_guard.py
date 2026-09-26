"""One guard against a model calling the same tool with the same arguments
over and over — for every tool, current and future.

Live loops it replaces one tool at a time: meta_insights_query x47 on a
permission error, catalogue_resolve_brand x30 on an unknown brand,
query_metrics repeating a successful fetch. Each got its own ad-hoc stop (or
none). Here: the 2nd identical call returns the result it already has, marked
as a repeat; the 3rd also withdraws that tool for the rest of the mission
(``limits.withdraw_tool``), so the model can only move on or answer.

A failure the tool marked ``retryable`` is re-executed instead of replayed —
a transient MCP error deserves a real retry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.limits import withdraw_tool
from seleric_swarm.agent.output import ToolResult

WITHDRAW_AFTER = 3


def _args_key(args: Any) -> str:
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(args)


def _replay(prior: Any, tool: str, n: int) -> Any:
    if not isinstance(prior, ToolResult):
        return prior
    note = (
        f"REPEATED CALL ({n}x, identical arguments) — {tool} returns the same result "
        "every time; it is shown again below. "
        + (
            f"{tool} is now withdrawn for this mission: use what you have, try a "
            "different approach, or answer."
            if n >= WITHDRAW_AFTER
            else "Do not call it again with these arguments."
        )
    )
    return prior.model_copy(update={"summary": f"{note} {prior.summary}"})


@dataclass
class RepeatCallGuard(AbstractCapability[SelericDeps]):
    _results: dict[tuple[str, str], Any] = field(default_factory=dict)

    @classmethod
    def get_serialization_name(cls) -> str | None:
        return None

    async def wrap_tool_execute(
        self,
        ctx: RunContext[SelericDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Any,
    ) -> Any:
        counts = getattr(ctx.deps, "call_counts", None)
        if counts is None or tool_def.kind != "function":
            return await handler(args)
        key = f"{tool_def.name}:{_args_key(args)}"
        slot = (str(getattr(ctx.deps, "mission_id", "")), key)
        n = counts.get(f"repeat:{key}", 0) + 1
        counts[f"repeat:{key}"] = n
        prior = self._results.get(slot)
        rerun = prior is None or (
            isinstance(prior, ToolResult) and not prior.success and prior.retryable
        )
        if rerun:
            result = await handler(args)
            self._results[slot] = result
            return result
        if n >= WITHDRAW_AFTER:
            withdraw_tool(ctx.deps, tool_def.name)
        return _replay(prior, tool_def.name, n)

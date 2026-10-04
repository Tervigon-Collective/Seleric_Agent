"""Live progress from a running mission, so the UI can show steps as they happen.

The agent loop lives far from the conversation layer that owns the event
stream. Rather than thread a callback through every signature, the layer that
owns the run registers a sink for the mission id, and the agent loop emits to
it. With no sink registered, nothing is emitted and the run is unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    AgentStreamEvent,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    ThinkingPart,
    ThinkingPartDelta,
)

_log = logging.getLogger("seleric.agent.progress")

# event_type, human summary, payload
ProgressSink = Any

_SINKS: dict[str, ProgressSink] = {}

_TOOL_LABELS: dict[str, str] = {
    "search_semantics": "Searching the metric catalogue",
    "list_metrics": "Listing the available metrics",
    "get_metric_definitions": "Reading metric definitions",
    "query_metrics": "Fetching metric data",
    "drilldown": "Drilling into segments",
    "compare_periods": "Comparing periods",
    "detect_anomalies": "Checking for anomalies",
    "diagnose_metric_change": "Diagnosing what changed and why",
    "estimate_effect": "Estimating causal effect",
    "refute_estimate": "Stress-testing the estimate",
    "forecast": "Forecasting",
    "search_knowledge": "Searching the knowledge base",
}


def set_progress_sink(mission_id: str, sink: ProgressSink) -> None:
    _SINKS[mission_id] = sink


def clear_progress_sink(mission_id: str) -> None:
    _SINKS.pop(mission_id, None)


def has_progress_sink(mission_id: str) -> bool:
    return mission_id in _SINKS


def emit_progress(mission_id: str, event_type: str, summary: str, payload: dict[str, Any]) -> None:
    sink = _SINKS.get(mission_id)
    if sink is None:
        return
    try:
        sink(event_type, summary, payload)
    except Exception:
        # Progress is best-effort: a UI/transport failure never fails the mission.
        _log.warning("progress_emit_failed", exc_info=True)


def tool_label(tool_name: str) -> str:
    return _TOOL_LABELS.get(tool_name) or tool_name.replace("_", " ").capitalize()


_PROBLEM_MAX_CHARS = 160


def _tool_problem(part: Any) -> str | None:
    """Why a tool call produced nothing usable, or None when it succeeded.

    Live 2026-10-04 (MS3-34e7eb26aa): seventeen refused generate_visualization
    calls were each reported "— done", so the timeline showed success while the
    model looped on a refusal. A refusal is a ``ToolResult(success=False)``; a
    rejected argument set comes back as a retry prompt instead of a return.
    """
    if getattr(part, "part_kind", "") == "retry-prompt":
        text = str(getattr(part, "content", "") or "invalid arguments")
    else:
        content = getattr(part, "content", None)
        if getattr(content, "success", True) is not False:
            return None
        text = str(getattr(content, "summary", "") or getattr(content, "error_code", "") or "no result")
    text = " ".join(text.split())
    return text if len(text) <= _PROBLEM_MAX_CHARS else text[: _PROBLEM_MAX_CHARS - 1] + "…"


def progress_handler(mission_id: str):
    """An ``event_stream_handler`` that reports each tool call and its outcome."""

    async def handler(_ctx: RunContext[Any], events: AsyncIterable[AgentStreamEvent]) -> None:
        async for event in events:
            if isinstance(event, FinalResultEvent):
                emit_progress(mission_id, "agent.answering", "Writing the answer", {})
            elif isinstance(event, FunctionToolCallEvent):
                name = event.part.tool_name
                emit_progress(
                    mission_id,
                    "agent.tool_started",
                    tool_label(name),
                    {"tool": name, "tool_call_id": event.part.tool_call_id},
                )
            elif isinstance(event, FunctionToolResultEvent):
                name = getattr(event.part, "tool_name", None) or ""
                problem = _tool_problem(event.part)
                emit_progress(
                    mission_id,
                    "agent.tool_completed",
                    f"{tool_label(name)} — " + (f"failed: {problem}" if problem else "done"),
                    {"tool": name, "tool_call_id": event.part.tool_call_id, "success": not problem},
                )
            elif isinstance(event, (PartDeltaEvent, PartStartEvent)):
                # A replayed step (model_health.ReplayedStreamedResponse)
                # delivers its reasoning as one whole part, a live one as deltas.
                if isinstance(event, PartStartEvent):
                    delta = event.part.content if isinstance(event.part, ThinkingPart) else ""
                elif isinstance(event.delta, ThinkingPartDelta):
                    delta = event.delta.content_delta or ""
                else:
                    delta = ""
                if delta:
                    emit_progress(
                        mission_id,
                        "agent.thinking_delta",
                        "Thinking…",
                        {"delta": delta},
                    )

    return handler

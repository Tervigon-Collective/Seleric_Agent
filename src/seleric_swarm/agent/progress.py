"""Live progress from a running mission, so the UI can show steps as they happen.

The agent loop lives far from the conversation layer that owns the event
stream. Rather than thread a callback through every signature, the layer that
owns the run registers a sink for the mission id, and the agent loop emits to
it. With no sink registered, nothing is emitted and the run is unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterable, Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    AgentStreamEvent,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)

_log = logging.getLogger("seleric.agent.progress")

# event_type, human summary, payload
ProgressSink = Any

_SINKS: dict[str, ProgressSink] = {}

_TOOL_LABELS: dict[str, str] = {
    "catalogue_search_metrics": "Finding the metrics in the catalogue",
    "catalogue_get_metrics": "Reading metric definitions",
    "catalogue_get_metric": "Reading a metric definition",
    "metrics_query": "Fetching metric data",
    "metrics_drilldown": "Drilling into segments",
    "insights_explain": "Explaining the movement",
    "diagnose_metric_change": "Diagnosing what changed and why",
    "break_down_metric": "Breaking the metric down",
    "explore_data": "Exploring the data",
    "forecast_metrics": "Forecasting",
    "run_python": "Calculating over the fetched data",
    "show_chart": "Drawing a chart",
    "business_snapshot": "Reading the business snapshot",
    "escalate": "Thinking harder about this",
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


def _tool_problem(part: Any) -> tuple[str | None, bool]:
    """Why a tool call produced nothing usable, or None when it succeeded.
    Returns (problem_text, is_retry).
    """
    if getattr(part, "part_kind", "") == "retry-prompt":
        text = str(getattr(part, "content", "") or "invalid arguments")
        text = " ".join(text.split())
        return (text if len(text) <= _PROBLEM_MAX_CHARS else text[: _PROBLEM_MAX_CHARS - 1] + "…", True)
    else:
        content = getattr(part, "content", None)
        if isinstance(content, str):  # an MCP tool returns its result as JSON text; {"error": ...} is a failed call
            try:
                body = json.loads(content)
            except ValueError:
                return None, False
            if isinstance(body, dict) and body.get("error"):
                text = " ".join(str(body["error"]).split())
                return (text if len(text) <= _PROBLEM_MAX_CHARS else text[: _PROBLEM_MAX_CHARS - 1] + "…", False)
            return None, False
        if getattr(content, "success", True) is not False:
            return None, False
        text = str(getattr(content, "summary", "") or getattr(content, "error_code", "") or "no result")
        text = " ".join(text.split())
        return (text if len(text) <= _PROBLEM_MAX_CHARS else text[: _PROBLEM_MAX_CHARS - 1] + "…", False)


class AnswerStream:
    """Forwards the model's answer text to ``on_stream`` while it is generated, in small coalesced chunks.

    What reached the user is remembered, so the end of the turn can skip a repeat when the final text is what was
    streamed and send ``reset`` + the final text when it is not (ids replaced, a correction pass, a note added)."""

    FLUSH_CHARS = 48
    FLUSH_S = 0.12

    def __init__(self, on_stream: Callable[[str, str], None], clock: Callable[[], float] = time.monotonic) -> None:
        self._on_stream = on_stream
        self._clock = clock
        self._sent: list[str] = []
        self._buffer: list[str] = []
        self._last_flush = clock()
        self.first_text_at: float | None = None

    def text(self, delta: str) -> None:
        if not delta:
            return
        if self.first_text_at is None:
            self.first_text_at = self._clock()
        self._buffer.append(delta)
        if sum(len(b) for b in self._buffer) >= self.FLUSH_CHARS or self._clock() - self._last_flush >= self.FLUSH_S:
            self.flush()

    def flush(self) -> None:
        if self._buffer:
            chunk = "".join(self._buffer)
            self._buffer.clear()
            self._sent.append(chunk)
            self._on_stream("delta", chunk)
        self._last_flush = self._clock()

    def discard(self) -> None:
        """What was shown is not the answer (tool calls followed it, or it is being redone)."""
        self._buffer.clear()
        if self._sent:
            self._sent.clear()
            self._on_stream("reset", "")

    def finish(self, answer: str) -> None:
        """The turn's final text: nothing if the user already has exactly it, else replace it."""
        self.flush()
        shown = "".join(self._sent)
        if shown.strip() == answer.strip():
            return
        self.discard()
        if answer.strip():
            self._sent.append(answer)
            self._on_stream("delta", answer)


def _feed_answer(event: Any, answer: AnswerStream, state: dict[str, Any]) -> None:
    """One stream event of a model response -> the answer stream. State is per response."""
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        state["held"].append(event.part.content or "")
    elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        state["held"].append(event.delta.content_delta or "")
    elif isinstance(event, FinalResultEvent):
        state["answering"] = True
    elif isinstance(event, FunctionToolCallEvent):
        # tool calls follow the text of this response: it was narration, not the answer
        state["held"].clear()
        state["answering"] = False
        answer.discard()
        return
    if state["answering"] and state["held"]:
        for chunk in state["held"]:
            answer.text(chunk)
        state["held"].clear()


def progress_handler(mission_id: str, answer: AnswerStream | None = None):
    """An ``event_stream_handler`` that reports each tool call and its outcome.

    ``answer`` also receives the model's text as it is generated. Text is held back until the response is known to
    be an answer (``FinalResultEvent``) and withdrawn if tool calls follow it."""

    async def handler(_ctx: RunContext[Any], events: AsyncIterable[AgentStreamEvent]) -> None:
        state: dict[str, Any] = {"held": [], "answering": False}
        async for event in events:
            if answer is not None:
                _feed_answer(event, answer, state)
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
                problem, is_retry = _tool_problem(event.part)
                if is_retry:
                    emit_progress(
                        mission_id,
                        "agent.tool_revising",
                        f"{tool_label(name)} — re-evaluating: {problem}",
                        {"tool": name, "tool_call_id": event.part.tool_call_id, "success": False, "is_retry": True},
                    )
                else:
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
        if answer is not None:
            answer.flush()

    return handler

"""The understand call with its failure policy (A4): one retry, then a Clarification.

The model call itself is injected (``DraftLLM``), so this package stays free of LLM SDKs
(A1/A2). ``adapters.understand_llm`` provides the pydantic-ai implementation.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from seleric_swarm.contracts.pipeline import Clarification, QuestionDraft

_log = logging.getLogger("seleric.understand")

_UNAVAILABLE = Clarification(
    reason="understanding_unavailable",
    question_to_user="I could not read that question just now. Could you ask it again, perhaps in other words?",
)


class DraftLLM(Protocol):
    """One structured LLM call that returns a draft or a clarification."""

    async def __call__(self, *, instructions: str, prompt: str) -> tuple[QuestionDraft | Clarification, dict[str, Any]]: ...


async def call_understand(
    llm: DraftLLM | None, *, instructions: str, prompt: str
) -> tuple[QuestionDraft | Clarification, dict[str, Any]]:
    """Run ``llm`` with one retry. Never raises: a second failure is a Clarification."""
    if llm is None:
        return _UNAVAILABLE, {"status": "skipped"}
    started = time.perf_counter()
    stats: dict[str, Any] = {}
    for attempt in (1, 2):
        try:
            output, usage = await llm(instructions=instructions, prompt=prompt)
        except Exception as exc:
            _log.warning("understand_failed attempt=%d", attempt, exc_info=True)
            stats.update(status="failed", error=type(exc).__name__, attempt=attempt)
            continue
        stats.update(usage, attempt=attempt)
        if isinstance(output, Clarification):
            stats["status"] = "clarification"
        else:
            stats.update(status="ok", kind=output.kind, shape=output.shape)
        stats["latency_ms"] = round((time.perf_counter() - started) * 1000)
        return output, stats
    stats["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return _UNAVAILABLE, stats

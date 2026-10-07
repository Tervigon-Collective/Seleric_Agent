"""Per-step context management for the agent loop (2026-10-07).

Every model step re-sent the whole conversation: 6.4k tokens of instructions,
6k of tool schemas and every earlier tool result in full, so steps grew from 12k
to 57k input tokens and one mission spent 400-700k tokens — past the deployment's
per-minute token quota, hence the 429s and the 80-200s missions.

``compact_history`` keeps the latest exchanges verbatim and collapses older tool
returns to the tool's own one-line summary plus artifact ids. Nothing is lost: the
full rows live in the evidence store, the established values ride the working-memory
scratchpad on every turn, and the artifact ids stay citable.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

# Messages at the end of the history that are always sent verbatim (the latest
# request/response exchanges: what the model is acting on right now).
KEEP_TAIL_MESSAGES = 4
# A tool return longer than this, outside the tail, is replaced by its digest.
DIGEST_OVER_CHARS = 700
_SUMMARY_CHARS = 480
_IDS_SHOWN = 12


def _digest(content: Any) -> str | None:
    """One line for a tool return: the tool's own summary and its artifact ids."""
    if hasattr(content, "model_dump"):
        content = content.model_dump(mode="json")
    if isinstance(content, dict):
        summary = str(content.get("summary") or "")
        ids = [str(i) for i in content.get("artifact_ids") or []]
        if not summary and not ids:
            return None
        shown = ", ".join(ids[:_IDS_SHOWN]) + (f" (+{len(ids) - _IDS_SHOWN} more)" if len(ids) > _IDS_SHOWN else "")
        cut = summary if len(summary) <= _SUMMARY_CHARS else summary[: _SUMMARY_CHARS - 1] + "…"
        return f"[earlier result, compacted] {cut}" + (f" artifact_ids: {shown}" if shown else "")
    text = str(content)
    return f"[earlier result, compacted] {text[: _SUMMARY_CHARS - 1]}…"


def compact_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Collapse long tool returns outside the latest exchanges to their digests."""
    if len(messages) <= KEEP_TAIL_MESSAGES:
        return messages
    head, tail = messages[:-KEEP_TAIL_MESSAGES], messages[-KEEP_TAIL_MESSAGES:]
    out: list[ModelMessage] = []
    for message in head:
        if not isinstance(message, ModelRequest):
            out.append(message)
            continue
        parts = []
        changed = False
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and len(str(part.content)) > DIGEST_OVER_CHARS:
                digest = _digest(part.content)
                if digest is not None:
                    parts.append(dataclasses.replace(part, content=digest))
                    changed = True
                    continue
            parts.append(part)
        out.append(dataclasses.replace(message, parts=parts) if changed else message)
    return [*out, *tail]

"""LiveKit-facing context projection for a voice session.

The conversational voice LLM must not receive the full ContextBundle (memories
payloads, tool results, provenance). It gets a short, speakable projection:

- last few thread turns (truncated text only)
- durable decisions / preferences / constraints (short lines)
- never tool payloads, artifact bodies, or raw evidence

Built from conversation API JSON so the voice worker stays a client of the
same durable thread the typed UI uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Keep the voice model small: it only needs enough to resolve follow-ups.
MAX_TURNS = 4  # (user, assistant) pairs → up to 8 chat messages
MAX_TURN_CHARS = 400
MAX_DURABLE = 5
MAX_DURABLE_CHARS = 160
_DURABLE_TYPES = frozenset({"PREFERENCE", "DECISION", "CONSTRAINT", "DEFINITION"})


def _part_text(message: dict[str, Any]) -> str:
    chunks: list[str] = []
    for part in message.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "").upper() != "TEXT":
            continue
        content = part.get("content")
        if isinstance(content, str) and content.strip():
            chunks.append(content.strip())
    return "\n".join(chunks)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _role(message: dict[str, Any]) -> str | None:
    role = str(message.get("role") or "").upper()
    if role == "USER":
        return "user"
    if role == "ASSISTANT":
        return "assistant"
    return None


@dataclass(frozen=True)
class VoiceProjection:
    """What the LiveKit voice agent is allowed to know at connect time."""

    turns: tuple[tuple[str, str], ...] = ()
    durable_notes: tuple[str, ...] = ()
    composition: dict[str, Any] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.turns and not self.durable_notes

    def instructions_suffix(self) -> str:
        if not self.durable_notes:
            return ""
        lines = "\n".join(f"- {note}" for note in self.durable_notes)
        return (
            "\n\nDurable context from this conversation (use only to resolve "
            "references; never invent numbers from it):\n" + lines
        )

    def augment_instructions(self, base: str) -> str:
        return base + self.instructions_suffix()

    def build_chat_ctx(self) -> Any | None:
        """LiveKit ``ChatContext`` seeded with prior turns, or ``None`` if empty."""
        if not self.turns:
            return None
        from livekit.agents.llm import ChatContext

        ctx = ChatContext.empty() if hasattr(ChatContext, "empty") else ChatContext()
        for role, text in self.turns:
            ctx.add_message(role=role, content=text)
        return ctx


def _memory_in_scope(raw: dict[str, Any], thread_id: str | None) -> bool:
    scope = str(raw.get("scope") or "").upper()
    if scope in {"", "USER"}:
        return True
    if scope == "THREAD":
        return not thread_id or str(raw.get("thread_id") or "") == thread_id
    # PROJECT / EPISODIC: include when present; voice does not resolve project yet.
    return scope in {"PROJECT", "EPISODIC"}


def project_voice_context(
    *,
    messages: list[dict[str, Any]] | None = None,
    memories: list[dict[str, Any]] | None = None,
    thread_id: str | None = None,
) -> VoiceProjection:
    """Build a voice projection from conversation API payloads."""

    turns: list[tuple[str, str]] = []
    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        role = _role(raw)
        text = _part_text(raw)
        if not role or not text:
            continue
        # Skip pending assistant placeholders ("Working…").
        if role == "assistant" and text.casefold() in {"working…", "working...", "working"}:
            continue
        turns.append((role, _truncate(text, MAX_TURN_CHARS)))
    # Keep the most recent pairs only.
    turns = turns[-2 * MAX_TURNS :]

    durable: list[str] = []
    memory_ids: list[str] = []
    for raw in memories or []:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "").upper()
        if status and status != "ACTIVE":
            continue
        if not _memory_in_scope(raw, thread_id):
            continue
        mem_type = str(raw.get("type") or "").upper()
        if mem_type not in _DURABLE_TYPES:
            continue
        content = raw.get("content")
        text = content if isinstance(content, str) else str(content or "")
        text = _truncate(text.strip(), MAX_DURABLE_CHARS)
        if not text:
            continue
        durable.append(f"{mem_type.lower()}: {text}")
        if raw.get("id"):
            memory_ids.append(str(raw["id"]))
        if len(durable) >= MAX_DURABLE:
            break

    return VoiceProjection(
        turns=tuple(turns),
        durable_notes=tuple(durable),
        composition={
            "builder": "voice-projection-v1",
            "turn_count": len(turns),
            "durable_count": len(durable),
            "memory_ids": memory_ids,
            "message_roles": [role for role, _ in turns],
            "thread_id": thread_id,
        },
    )


async def fetch_voice_projection(
    client: Any,
    *,
    api_url: str,
    headers: dict[str, str],
    thread_id: str,
) -> VoiceProjection:
    """Load messages + durable memories for ``thread_id``; fail open to empty."""

    base = api_url.rstrip("/")
    messages: list[dict[str, Any]] = []
    memories: list[dict[str, Any]] = []
    try:
        res = await client.get(
            f"{base}/v1/threads/{thread_id}/messages",
            headers=headers,
            params={"limit": 24},
        )
        if res.is_success:
            payload = res.json()
            if isinstance(payload, list):
                messages = [m for m in payload if isinstance(m, dict)]
    except Exception:
        messages = []
    try:
        # No thread_id filter: USER preferences are cross-thread; we scope client-side.
        res = await client.get(
            f"{base}/v1/memories",
            headers=headers,
            params={"limit": 40},
        )
        if res.is_success:
            payload = res.json()
            if isinstance(payload, list):
                memories = [m for m in payload if isinstance(m, dict)]
    except Exception:
        memories = []
    return project_voice_context(messages=messages, memories=memories, thread_id=thread_id)

"""Voice agent (LiveKit) — Phase 0.

Two halves with deliberately different dependencies:

- ``token`` — mints LiveKit room JWTs. Needs only ``livekit-api`` (the
  ``voice-api`` extra) and is exercised by the normal test suite.
- ``worker`` — the long-lived LiveKit agent process. Needs ``livekit-agents``
  (the ``voice`` extra) plus live credentials, and is started by the
  ``seleric-voice`` console script, never by the API.

Design and phasing: ``docs/features/voice-agent/``.
"""

from __future__ import annotations

__all__ = ["room_name_for_thread"]


def room_name_for_thread(thread_id: str) -> str:
    """Stable room name for a thread.

    The same thread always maps to the same room, so a network blip rejoins
    the call already in progress. A new call has to delete that room first:
    LiveKit only dispatches the agent when the room is created, and a room
    left behind by a hangup would otherwise accept the next join with no agent.
    """

    cleaned = thread_id.strip()
    if not cleaned:
        raise ValueError("thread_id must not be blank")
    return f"seleric-voice-{cleaned}"

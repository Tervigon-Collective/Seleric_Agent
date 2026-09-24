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
    """Deterministic room name for a thread.

    Deterministic so a reconnecting browser rejoins the room it left without
    server-side session state. Thread ids are already opaque, so this leaks
    nothing a holder of the id does not have.
    """

    cleaned = thread_id.strip()
    if not cleaned:
        raise ValueError("thread_id must not be blank")
    return f"seleric-voice-{cleaned}"

"""Agent transport abstraction (architecture sec. 1, 26).

Agent logic must not know whether a peer is local or remote. ``InProcessTransport``
dispatches to handlers registered in the same process now; an ``A2AHttpTransport``
that speaks A2A-over-HTTP is a drop-in later.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from seleric_swarm.swarm.envelope import SwarmMessage

Handler = Callable[[SwarmMessage], Awaitable[dict[str, Any]]]


class AgentTransport(Protocol):
    async def send(self, message: SwarmMessage) -> dict[str, Any]:
        """Deliver a message to ``message.to_agent`` and return its artifact response."""
        ...


class InProcessTransport:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self.log: list[dict[str, Any]] = []

    def register(self, agent_id: str, handler: Handler) -> None:
        self._handlers[agent_id] = handler

    def known(self) -> list[str]:
        return sorted(self._handlers)

    async def send(self, message: SwarmMessage) -> dict[str, Any]:
        self.log.append(
            {
                "from": message.from_agent,
                "to": message.to_agent,
                "intent": message.intent.value,
                "objective": message.objective,
            }
        )
        handler = self._handlers.get(message.to_agent or "")
        if handler is None:
            return {
                "ok": False,
                "error": f"no handler registered for agent '{message.to_agent}'",
            }
        return await handler(message)

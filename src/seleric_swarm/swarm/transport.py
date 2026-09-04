"""Agent transport abstraction (architecture sec. 1, 26).

Agent logic must not know whether a peer is local or remote.
``InProcessTransport`` dispatches to handlers in-process;
``A2AHttpTransport`` speaks A2A-over-HTTP to remote agent endpoints;
``HybridTransport`` tries local handlers first, then HTTP.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

import httpx

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
                "intent": message.intent.value if hasattr(message.intent, "value") else message.intent,
                "objective": message.objective,
                "transport": "inprocess",
            }
        )
        handler = self._handlers.get(message.to_agent or "")
        if handler is None:
            return {
                "ok": False,
                "error": f"no handler registered for agent '{message.to_agent}'",
                "error_code": "AGENT_UNAVAILABLE",
            }
        return await handler(message)


class A2AHttpTransport:
    """HTTP A2A transport — POST SwarmMessage JSON to remote agent endpoints.

    Endpoint resolution order:
    1. ``endpoints[to_agent]`` if provided
    2. ``{base_url}/a2a/v1/agents/{to_agent}/messages``
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        endpoints: Mapping[str, str] | None = None,
        timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoints = dict(endpoints or {})
        self.timeout_s = timeout_s
        self._client = client
        self._owns_client = client is None
        self._headers = dict(headers or {})
        self.log: list[dict[str, Any]] = []

    def _url_for(self, agent_id: str) -> str:
        if agent_id in self.endpoints:
            return self.endpoints[agent_id]
        return f"{self.base_url}/a2a/v1/agents/{agent_id}/messages"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(self, message: SwarmMessage) -> dict[str, Any]:
        agent_id = message.to_agent or ""
        url = self._url_for(agent_id)
        payload = message.model_dump(mode="json")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **self._headers,
        }
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key
        headers["X-Seleric-Mission-Id"] = message.mission_id
        headers["X-Seleric-From-Agent"] = message.from_agent

        self.log.append(
            {
                "from": message.from_agent,
                "to": message.to_agent,
                "intent": message.intent.value if hasattr(message.intent, "value") else message.intent,
                "objective": message.objective,
                "transport": "http",
                "url": url,
            }
        )

        try:
            client = await self._get_client()
            resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            return {
                "ok": False,
                "error": f"A2A timeout contacting {agent_id}: {exc}",
                "error_code": "TIMEOUT",
                "artifact_refs": [],
            }
        except httpx.HTTPError as exc:
            return {
                "ok": False,
                "error": f"A2A network error contacting {agent_id}: {exc}",
                "error_code": "A2A_ERROR",
                "artifact_refs": [],
            }

        if resp.status_code >= 500:
            return {
                "ok": False,
                "error": f"A2A server error {resp.status_code} from {agent_id}",
                "error_code": "SERVICE_UNAVAILABLE",
                "artifact_refs": [],
            }
        if resp.status_code == 404:
            return {
                "ok": False,
                "error": f"A2A agent endpoint not found for '{agent_id}'",
                "error_code": "AGENT_UNAVAILABLE",
                "artifact_refs": [],
            }
        if resp.status_code >= 400:
            return {
                "ok": False,
                "error": f"A2A client error {resp.status_code}: {resp.text[:200]}",
                "error_code": "A2A_ERROR",
                "artifact_refs": [],
            }

        try:
            data = resp.json()
        except ValueError:
            return {
                "ok": False,
                "error": "A2A response was not JSON",
                "error_code": "SCHEMA_ERROR",
                "artifact_refs": [],
            }
        if not isinstance(data, dict):
            return {"ok": True, "artifact_refs": [], "payload": data}
        data.setdefault("ok", True)
        data.setdefault("artifact_refs", list(data.get("artifact_refs") or []))
        return data


class HybridTransport:
    """Prefer in-process handlers; fall back to HTTP for unknown agents."""

    def __init__(
        self,
        local: InProcessTransport,
        remote: A2AHttpTransport,
    ) -> None:
        self.local = local
        self.remote = remote
        self.log: list[dict[str, Any]] = []

    def register(self, agent_id: str, handler: Handler) -> None:
        self.local.register(agent_id, handler)

    def known(self) -> list[str]:
        return self.local.known()

    async def send(self, message: SwarmMessage) -> dict[str, Any]:
        agent_id = message.to_agent or ""
        if agent_id in self.local._handlers:
            result = await self.local.send(message)
            self.log.extend(self.local.log[-1:])
            return result
        result = await self.remote.send(message)
        self.log.extend(self.remote.log[-1:])
        return result


def build_transport(
    *,
    mode: str = "inprocess",
    base_url: str = "http://localhost:8000",
    endpoints: Mapping[str, str] | None = None,
    timeout_s: float = 30.0,
    client: httpx.AsyncClient | None = None,
) -> InProcessTransport | A2AHttpTransport | HybridTransport:
    """Factory for coordinator / swarm orchestrators."""
    mode = (mode or "inprocess").lower()
    local = InProcessTransport()
    if mode == "inprocess":
        return local
    remote = A2AHttpTransport(
        base_url=base_url, endpoints=endpoints, timeout_s=timeout_s, client=client
    )
    if mode == "http":
        return remote
    if mode == "hybrid":
        return HybridTransport(local, remote)
    return local

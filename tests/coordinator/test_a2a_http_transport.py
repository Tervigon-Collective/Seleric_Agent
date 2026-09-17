"""A2A HTTP transport tests."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from seleric_swarm.coordinator.contracts import AgentContext, TaskSpec
from seleric_swarm.coordinator.routing.invocation import A2AAgentInvoker
from seleric_swarm.swarm.envelope import Intent, SwarmMessage
from seleric_swarm.swarm.transport import (
    A2AHttpTransport,
    HybridTransport,
    InProcessTransport,
    build_transport,
)


def _message(to: str = "diagnostic_agent") -> SwarmMessage:
    return SwarmMessage.request(
        mission_id="MS-1",
        from_agent="coordinator_agent",
        to_agent=to,
        intent=Intent.MODEL_REQUEST,
        objective="diagnose",
        idempotency_key="idem-1",
    )


@pytest.mark.asyncio
async def test_a2a_http_transport_success():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["to_agent"] == "diagnostic_agent"
        assert request.headers.get("Idempotency-Key") == "idem-1"
        return httpx.Response(200, json={"ok": True, "artifact_refs": ["HYP-1", "CAUS-1"]})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    a2a = A2AHttpTransport(base_url="http://test", client=client)
    result = await a2a.send(_message())
    assert result["ok"] is True
    assert result["artifact_refs"] == ["HYP-1", "CAUS-1"]
    assert a2a.log[-1]["transport"] == "http"
    await client.aclose()


@pytest.mark.asyncio
async def test_a2a_http_timeout_is_retryable_code():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a2a = A2AHttpTransport(base_url="http://test", client=client)
    result = await a2a.send(_message())
    assert result["ok"] is False
    assert result["error_code"] == "TIMEOUT"
    await client.aclose()


@pytest.mark.asyncio
async def test_a2a_http_404_agent_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "missing"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a2a = A2AHttpTransport(base_url="http://test", client=client)
    result = await a2a.send(_message("missing_agent"))
    assert result["error_code"] == "AGENT_UNAVAILABLE"
    await client.aclose()


@pytest.mark.asyncio
async def test_hybrid_prefers_local_then_http():
    local = InProcessTransport()

    async def local_handler(msg: SwarmMessage) -> dict:
        return {"ok": True, "artifact_refs": ["EV-local"], "produced": "evidence"}

    local.register("observer_agent", local_handler)

    def http_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "artifact_refs": ["EV-remote"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(http_handler))
    remote = A2AHttpTransport(base_url="http://test", client=client)
    hybrid = HybridTransport(local, remote)

    local_result = await hybrid.send(_message("observer_agent"))
    assert local_result["artifact_refs"] == ["EV-local"]

    remote_result = await hybrid.send(_message("remote_agent"))
    assert remote_result["artifact_refs"] == ["EV-remote"]
    await client.aclose()


def test_build_transport_modes():
    assert isinstance(build_transport(mode="inprocess"), InProcessTransport)
    assert isinstance(build_transport(mode="http"), A2AHttpTransport)
    assert isinstance(build_transport(mode="hybrid"), HybridTransport)


@pytest.mark.asyncio
async def test_inprocess_transport_deduplicates_concurrent_requests():
    transport = InProcessTransport()
    calls = 0

    async def handler(_message: SwarmMessage) -> dict:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"ok": True, "artifact_refs": ["EV-once"]}

    transport.register("diagnostic_agent", handler)
    first, second = await asyncio.gather(
        transport.send(_message()),
        transport.send(_message()),
    )
    assert first == second
    assert calls == 1


@pytest.mark.asyncio
async def test_http_transport_deduplicates_repeated_requests():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True, "artifact_refs": ["EV-once"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = A2AHttpTransport(base_url="http://test", client=client)
    assert await transport.send(_message()) == await transport.send(_message())
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_a2a_response_is_not_permanently_deduplicated():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                503,
                headers={"Retry-After": "1"},
                json={"ok": False},
            )
        return httpx.Response(200, json={"ok": True, "artifact_refs": ["EV-recovered"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = A2AHttpTransport(base_url="http://test", client=client)
    first = await transport.send(_message())
    second = await transport.send(_message())
    assert first["ok"] is False
    assert first["retry_after"] == "1"
    assert second["ok"] is True
    assert calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_invoker_treats_ok_false_as_failure():
    class RejectingTransport:
        async def send(self, _message: SwarmMessage) -> dict:
            return {
                "ok": False,
                "error_code": "SERVICE_UNAVAILABLE",
                "error": "try later",
                "retry_after": "2",
            }

    task = TaskSpec(
        task_id="T-reject",
        mission_id="MS-1",
        task_type="diagnostic",
        objective="diagnose",
    )
    context = AgentContext(
        mission_id="MS-1",
        task_id=task.task_id,
        question=task.objective,
    )
    result = await A2AAgentInvoker(RejectingTransport()).invoke(
        "diagnostic_agent", task, context
    )
    assert result.status == "retryable_failure"
    assert result.error_code == "SERVICE_UNAVAILABLE"
    assert result.metadata["retry_after"] == "2"


@pytest.mark.asyncio
async def test_invoker_places_idempotency_key_on_envelope():
    sent: list[SwarmMessage] = []

    class CaptureTransport:
        async def send(self, message: SwarmMessage) -> dict:
            sent.append(message)
            return {"ok": True, "artifact_refs": []}

    task = TaskSpec(
        task_id="T-1",
        mission_id="MS-1",
        task_type="diagnostic",
        objective="diagnose",
        idempotency_key="task-once",
    )
    context = AgentContext(
        mission_id="MS-1",
        task_id="T-1",
        question="diagnose",
    )
    await A2AAgentInvoker(CaptureTransport()).invoke(
        "diagnostic_agent", task, context
    )
    assert sent[0].idempotency_key == "task-once"
    assert "idempotency_key" not in sent[0].payload

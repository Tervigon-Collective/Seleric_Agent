"""V3 agent is reachable from the UI paths (conversations + office)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from seleric_swarm.agent.dependencies import NullMcpClient
from seleric_swarm.agent.runner import run_v3_mission
from seleric_swarm.api.office import registry
from seleric_swarm.api.v3_state import get_v3_mission_store, reset_v3_stores
from seleric_swarm.config.settings import Settings
from seleric_swarm.persistence.memory import InMemoryMissionStore


@pytest.fixture(autouse=True)
def _clean_v3_state():
    reset_v3_stores()
    registry.clear()
    yield
    reset_v3_stores()
    registry.clear()


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        settings=Settings(
            llm_provider="fake",
            v3_agent_enabled=True,
            azure_openai_api_key="",
            persistence_backend="memory",
            app_env="test",
        ),
        mcp=NullMcpClient(),
        store=InMemoryMissionStore(),
    )


@pytest.mark.asyncio
async def test_v3_runner_persists_for_conversations_and_office():
    runtime = _runtime()
    dispatched = await run_v3_mission(
        runtime,  # type: ignore[arg-type]
        query="what were net sales yesterday?",
        mission_id="MS3-ui-1",
        workspace_id="default",
        owner_user_id="default",
        thread_id="thread-ui",
        run_id="run-ui",
        request_id="req-ui",
    )
    assert dispatched["route"] == "v3"
    result = dispatched["result"]
    assert result["mission_id"] == "MS3-ui-1"
    assert result["final_response"]
    stored = runtime.store.get("MS3-ui-1")
    assert stored is not None
    assert stored.final_response == result["final_response"]
    raw = runtime.store.get_raw("MS3-ui-1")
    assert raw is not None
    assert raw["route"] == "v3"
    assert raw["mission_lead"] == "seleric_agent"
    v3 = get_v3_mission_store().get("MS3-ui-1")
    assert v3 is not None
    assert v3.status in {"partial", "completed", "failed"}


def test_v3_office_snapshot_and_list(monkeypatch):
    from seleric_swarm import main as main_mod
    from seleric_swarm.api.office import gateway

    runtime = _runtime()

    class _GatewayRuntime:
        store = runtime.store
        settings = runtime.settings

    monkeypatch.setattr(gateway, "_runtime", lambda: _GatewayRuntime())
    monkeypatch.setattr(main_mod, "_runtime", _GatewayRuntime())

    import asyncio

    asyncio.run(
        run_v3_mission(
            runtime,  # type: ignore[arg-type]
            query="units sold yesterday",
            mission_id="MS3-ui-2",
            workspace_id="default",
            owner_user_id="default",
            thread_id="thread-ui-2",
            run_id="run-ui-2",
            request_id="req-ui-2",
        )
    )
    client = TestClient(main_mod.app)
    listed = client.get("/v1/office/missions").json()
    ids = [m["missionId"] for m in listed["missions"]]
    assert "MS3-ui-2" in ids
    snap = client.get("/v1/office/missions/MS3-ui-2/snapshot").json()
    assert snap["missionId"] == "MS3-ui-2"
    assert snap["route"] == "v3"
    assert snap["finalResponse"]
    assert [a["agentId"] for a in snap["agents"]] == ["seleric_agent"]
    assert snap["agents"][0]["missionLead"] is True
    assert snap["leadAgentId"] == "seleric_agent"


def test_post_missions_uses_v3_when_enabled(monkeypatch):
    import seleric_swarm.main as main_mod
    from seleric_swarm.bootstrap import build_runtime

    settings = Settings(
        llm_provider="fake",
        v3_agent_enabled=True,
        azure_openai_api_key="",
        persistence_backend="memory",
        app_env="test",
        langsmith_tracing=False,
    )
    runtime = build_runtime(settings)
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(main_mod.app, raise_server_exceptions=True)
    response = client.post(
        "/v1/missions",
        json={
            "query": "hello from the office ui",
            "mode": "read_only",
            "wait": True,
            "scope": {"timezone": "Asia/Kolkata"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "v3"
    assert body["final_response"]
    got = client.get(f"/v1/missions/{body['mission_id']}")
    assert got.status_code == 200
    assert got.json()["route"] == "v3"


def test_rate_limit_error_is_not_dumped_to_the_user():
    # Classification is by exception type/attributes now (agent/runner.py's
    # own docstring: "not string content"), not by sniffing "429" out of a
    # message -- a real ModelHTTPError(429), not a RuntimeError that merely
    # mentions "429" in its text, is what the classifier actually matches.
    from pydantic_ai.exceptions import ModelHTTPError

    from seleric_swarm.agent.runner import _user_facing_agent_failure

    message, code = _user_facing_agent_failure(
        ModelHTTPError(429, "gpt-4o", body="RateLimitReached " + ("x" * 4000))
    )
    assert code == "LLM_RATE_LIMITED"
    assert "429" not in message
    assert "RateLimitReached" not in message
    assert "retry" in message.lower()








def test_dispatch_route_is_always_v3():
    from seleric_swarm.api.conversations import _dispatch_route

    assert _dispatch_route(SimpleNamespace(settings=SimpleNamespace(v3_agent_enabled=True))) == "v3"
    assert _dispatch_route(SimpleNamespace(settings=SimpleNamespace(v3_agent_enabled=False))) == "v3"
    assert _dispatch_route(SimpleNamespace()) == "v3"

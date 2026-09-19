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
    assert raw["mission_lead"] == "coordinator"
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
    coordinator = next(a for a in snap["agents"] if a["agentId"] == "coordinator")
    assert coordinator["missionLead"] is True


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
    from seleric_swarm.agent.runner import _user_facing_agent_failure

    message, code = _user_facing_agent_failure(
        RuntimeError("ModelHTTPError: status_code: 429 RateLimitReached " + ("x" * 4000))
    )
    assert code == "LLM_RATE_LIMITED"
    assert "429" not in message
    assert "RateLimitReached" not in message
    assert "retry" in message.lower()


def test_mission_prompt_pins_as_of_and_timezone():
    from seleric_swarm.agent.runner import _as_of_datetime, _mission_prompt

    as_of = _as_of_datetime("2026-09-19", "Asia/Kolkata")
    prompt = _mission_prompt("units sold yesterday", as_of, "Asia/Kolkata")
    assert "as_of=2026-09-19" in prompt
    assert "timezone=Asia/Kolkata" in prompt
    assert "'yesterday' is 2026-09-18" in prompt
    assert "'today' is 2026-09-19" in prompt
    assert as_of.tzinfo is not None
    assert getattr(as_of.tzinfo, "key", None) == "Asia/Kolkata"


@pytest.mark.asyncio
async def test_completed_with_empty_response_is_caught_not_shipped(monkeypatch):
    """Regression for a real bug observed live 2026-09-19 (TASK_SHEET.md):
    ``agent.run()`` called directly (not ``run_validated_mission``) let a
    ``status="completed"`` mission with an empty ``final_response`` reach a
    real user, because ``EvidenceValidator`` never ran on this path. A model
    that always returns exactly that shape must now surface as a validator
    failure, not ship the empty answer."""
    from datetime import UTC, datetime

    from pydantic_ai.models.test import TestModel

    from seleric_swarm.agent import runner as runner_mod

    def _broken_model(_settings: object) -> TestModel:
        return TestModel(
            call_tools=[],
            custom_output_args={
                "mission_id": "stub",
                "status": "completed",
                "query": "",
                "as_of": datetime.now(UTC),
                "final_response": "",
                "evidence_ids": [],
                "finding_ids": [],
                "limitations": [],
                "error_code": None,
                "trace": {},
            },
        )

    monkeypatch.setattr(runner_mod, "resolve_v3_model", _broken_model)

    runtime = _runtime()
    dispatched = await run_v3_mission(
        runtime,  # type: ignore[arg-type]
        query="what were net sales yesterday?",
        mission_id="MS3-empty-response",
    )
    result = dispatched["result"]
    assert result["status"] == "failed"
    assert result["error"]["code"] == "INSUFFICIENT_EVIDENCE"


def test_dispatch_route_follows_v3_flag():
    from seleric_swarm.api.conversations import _dispatch_route

    assert _dispatch_route(SimpleNamespace(settings=SimpleNamespace(v3_agent_enabled=True))) == "v3"
    assert _dispatch_route(SimpleNamespace(settings=SimpleNamespace(v3_agent_enabled=False))) == "swarm"
    assert _dispatch_route(SimpleNamespace()) == "swarm"

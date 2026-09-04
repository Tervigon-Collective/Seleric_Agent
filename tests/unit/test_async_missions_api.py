"""Async mission API (wait=false) regressions."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import seleric_swarm.main as main_mod
from seleric_swarm.api.async_missions import is_terminal_status
from seleric_swarm.main import app


@pytest.fixture
def client(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    return TestClient(app, raise_server_exceptions=True)


def test_async_mission_accept_then_poll_to_terminal(client):
    created = client.post(
        "/v1/missions",
        json={
            "query": "Why has CAC increased over the last three days?",
            "mode": "read_only",
            "scenario_id": "cac_regression",
            "wait": False,
            "full_diagnostic": True,
            "full_skeptic": True,
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "running"
    assert body.get("async") is True
    mission_id = body["mission_id"]
    assert mission_id.startswith("MS-")

    # TestClient runs BackgroundTasks after the response is sent.
    deadline = time.time() + 15
    final = None
    while time.time() < deadline:
        got = client.get(f"/v1/missions/{mission_id}")
        assert got.status_code == 200
        final = got.json()
        if is_terminal_status(final.get("status")):
            break
        time.sleep(0.05)
    assert final is not None
    assert is_terminal_status(final.get("status"))
    assert final.get("mission_id") == mission_id


def test_async_rejects_unknown_scenario(client):
    bad = client.post(
        "/v1/missions",
        json={
            "query": "Why has CAC increased?",
            "mode": "read_only",
            "scenario_id": "nope_xyz",
            "wait": False,
        },
    )
    assert bad.status_code == 400


def test_sync_wait_default_unchanged(client):
    created = client.post(
        "/v1/missions",
        json={
            "query": "What were net sales yesterday?",
            "mode": "read_only",
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
        },
    )
    assert created.status_code == 200
    assert created.json()["status"] != "running"


@pytest.mark.asyncio
async def test_run_mission_job_failure_path_marks_failed(runtime, monkeypatch):
    """The background worker's except-branch must not itself crash (regression:
    a `timezone: str` param used to shadow the `datetime.timezone` import, so
    `datetime.now(timezone.utc)` raised AttributeError and left the mission hung)."""
    from seleric_swarm.api import async_missions

    async def _boom(*_a, **_kw):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(async_missions, "run_any_mission", _boom)
    mid = "MS-failtest01"
    async_missions.seed_running_mission(
        runtime, mission_id=mid, query="why did cac increase", request_id="r1", session_id="s1"
    )
    await async_missions.run_mission_job(
        runtime,
        mission_id=mid,
        query="why did cac increase",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
        session_id="s1",
        request_id="r1",
        full_diagnostic=True,
        full_prediction=True,
        full_skeptic=True,
        scenario_id="cac_regression",
        execution_mode="fixture",
    )
    raw = runtime.store.get_raw(mid)
    assert raw is not None
    assert raw["status"] == "failed"
    assert raw.get("error_code") == "ASYNC_EXECUTION_FAILED"
    assert any("provider exploded" in lim for lim in raw.get("limitations", []))

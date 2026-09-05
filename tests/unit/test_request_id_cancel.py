"""Request ID + async cancel (v1.14)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import seleric_swarm.main as main_mod
from seleric_swarm.api.async_missions import (
    cancel_running_mission,
    clear_cancel,
    is_cancel_requested,
    seed_running_mission,
)
from seleric_swarm.api.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from seleric_swarm.main import app


def test_request_id_middleware_echo_and_mint():
    mini = FastAPI()

    @mini.get("/ping")
    def ping():
        return {"ok": True}

    mini.add_middleware(RequestIdMiddleware)
    client = TestClient(mini)
    minted = client.get("/ping")
    assert minted.status_code == 200
    assert minted.headers.get(REQUEST_ID_HEADER)

    echoed = client.get("/ping", headers={REQUEST_ID_HEADER: "abc123"})
    assert echoed.headers.get(REQUEST_ID_HEADER) == "abc123"


def test_main_app_sets_request_id(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    r = client.get("/health", headers={REQUEST_ID_HEADER: "corr-1"})
    assert r.status_code == 200
    assert r.headers.get(REQUEST_ID_HEADER) == "corr-1"


def test_cancel_running_mission(runtime):
    clear_cancel("MS-cancel1")
    seed_running_mission(
        runtime,
        mission_id="MS-cancel1",
        query="why cac?",
        request_id="r1",
        session_id="s1",
    )
    payload = cancel_running_mission(runtime, mission_id="MS-cancel1")
    assert payload["status"] == "cancelled"
    assert is_cancel_requested("MS-cancel1")
    with pytest.raises(ValueError, match="not cancellable"):
        cancel_running_mission(runtime, mission_id="MS-cancel1")
    # Failed second cancel must not leave a sticky process-local flag after clear.
    # Store still shows cancelled via runtime-aware check.
    assert is_cancel_requested("MS-cancel1", runtime) is True
    clear_cancel("MS-cancel1")


def test_cancel_endpoint(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    # Seed via store directly so we don't race background tasks
    seed_running_mission(
        runtime,
        mission_id="MS-api-cancel",
        query="why?",
        request_id="r",
        session_id="s",
    )
    cancelled = client.post("/v1/missions/MS-api-cancel/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    again = client.post("/v1/missions/MS-api-cancel/cancel")
    assert again.status_code == 409
    missing = client.post("/v1/missions/MS-does-not-exist/cancel")
    assert missing.status_code == 404
    clear_cancel("MS-api-cancel")

"""Sprint 1 scaffolding check for the V3 stub mission endpoint.

This exercises ``api/missions.py``'s ``APIRouter`` in isolation — it is
deliberately NOT mounted into ``main.py``'s app (see that module's
docstring). Sprint 5 defaults ``v3_agent_enabled`` to True; the 501 path
remains as a soak kill-switch when the flag is forced off.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api.missions import router
from seleric_swarm.api.v3_state import get_v3_mission_store
from seleric_swarm.config.settings import get_settings


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_enabled_by_default() -> None:
    get_settings.cache_clear()
    response = _client().post("/v1/missions", json={"query": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert body["mission_id"].startswith("MS3-")


def test_disabled_returns_501(monkeypatch) -> None:
    monkeypatch.setenv("V3_AGENT_ENABLED", "false")
    get_settings.cache_clear()
    response = _client().post("/v1/missions", json={"query": "hello"})
    assert response.status_code == 501


def test_stub_agent_responds_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("V3_AGENT_ENABLED", "true")
    get_settings.cache_clear()
    response = _client().post("/v1/missions", json={"query": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert "not exercised" in body["final_response"].lower()
    assert body["evidence_ids"] == []
    # The mission must be retrievable afterward -- an earlier version of
    # this handler discarded it entirely once the response was sent, which
    # also left the Office UI with nothing to read for this mission id.
    stored = get_v3_mission_store().get(body["mission_id"])
    assert stored is not None
    assert stored.status == "partial"
    assert stored.final_response == body["final_response"]

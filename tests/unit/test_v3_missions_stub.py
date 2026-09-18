"""Sprint 1 scaffolding check for the V3 stub mission endpoint.

This exercises ``api/missions.py``'s ``APIRouter`` in isolation — it is
deliberately NOT mounted into ``main.py``'s app (see that module's
docstring: 0% production traffic per the strangler-fig migration rule,
``docs/refactor/00_OVERVIEW.md`` §5).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api.missions import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_disabled_by_default() -> None:
    response = _client().post("/v1/missions", json={"query": "hello"})
    assert response.status_code == 501


def test_stub_agent_responds_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("V3_AGENT_ENABLED", "true")
    response = _client().post("/v1/missions", json={"query": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert "no toolsets" in body["final_response"].lower()
    assert body["evidence_ids"] == []

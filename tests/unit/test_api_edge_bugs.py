"""API edge-case / bugfix regression tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

import seleric_swarm.main as main_mod
from seleric_swarm.main import app


def test_api_rejects_empty_query(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)

    empty = client.post("/v1/missions", json={"query": "  ", "mode": "read_only"})
    assert empty.status_code == 400
    assert "non-empty" in empty.json()["detail"]

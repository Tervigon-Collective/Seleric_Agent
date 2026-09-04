"""Live-path API scenario matrix regressions for Coordinator V1.8+."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import seleric_swarm.main as main_mod
from seleric_swarm.main import app

_TERMINAL = {"completed", "prototype_completed", "partial", "blocked", "failed"}


@pytest.fixture
def client(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    return TestClient(app, raise_server_exceptions=True)


def test_why_cac_with_full_prediction_produces_forecast(client):
    """full_prediction=True must activate prediction even on a pure 'why' query."""
    r = client.post(
        "/v1/missions",
        json={
            "query": "Why has CAC increased over the last three days?",
            "mode": "read_only",
            "scenario_id": "cac_regression",
            "full_diagnostic": True,
            "full_prediction": True,
            "full_skeptic": True,
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in _TERMINAL
    assert body["status"] != "running"
    arts = body["artifacts"]
    assert arts["hypothesis"]
    assert arts["prediction"]
    assert arts["skeptic"]
    created = next(e for e in body["events"] if e.get("kind") == "mission_created")
    assert "diagnostic" in created["intents"]
    assert "predictive" in created["intents"]


def test_health_combo_never_returns_running(client):
    r = client.post(
        "/v1/missions",
        json={
            "query": "how are we doing today, what happens if this continues, and what should we do?",
            "mode": "read_only",
            "scenario_id": "cac_regression",
            "full_diagnostic": True,
            "full_prediction": True,
            "full_skeptic": True,
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in _TERMINAL
    assert body["artifacts"]["hypothesis"]
    assert body["artifacts"]["prediction"]
    assert body["artifacts"]["strategy"]
    assert body["artifacts"]["skeptic"]
    assert "not challenged" not in (body.get("final_response") or "")


def test_prescriptive_with_full_diagnostic_runs_diagnostic(client):
    r = client.post(
        "/v1/missions",
        json={
            "query": "what should we do about rising CAC?",
            "mode": "read_only",
            "scenario_id": "cac_regression",
            "full_diagnostic": True,
            "full_skeptic": True,
            "full_prediction": False,
            "scope": {"timezone": "Asia/Kolkata", "as_of": "2026-09-03"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in _TERMINAL
    assert body["artifacts"]["hypothesis"]
    assert body["artifacts"]["strategy"]
    assert body["artifacts"]["skeptic"]

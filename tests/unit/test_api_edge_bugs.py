"""API edge-case / bugfix regression tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import seleric_swarm.main as main_mod
from seleric_swarm.coordinator.governance.remediation import classify_followup
from seleric_swarm.main import app
from seleric_swarm.swarm.providers.errors import ScenarioNotFoundError
from seleric_swarm.swarm.providers.fixtures import list_scenarios, load_scenario


def test_load_scenario_unknown_raises():
    with pytest.raises(ScenarioNotFoundError) as ei:
        load_scenario("does_not_exist_xyz")
    assert "does_not_exist_xyz" in str(ei.value)
    assert list_scenarios()  # at least cac_regression exists


def test_classify_followup_skeptic_probes_are_hypothesis_tests():
    assert (
        classify_followup(
            {"question": "Compare metric.purchase_cvr for high vs low metric.mobile_lcp_seconds sessions"}
        )
        == "hypothesis_test"
    )
    assert (
        classify_followup({"question": "Check whether an unaffected control (e.g. desktop) moved as much."})
        == "hypothesis_test"
    )
    assert (
        classify_followup(
            {"question": "Verify metric.mobile_lcp_seconds change timestamp precedes the CVR change"}
        )
        == "hypothesis_test"
    )


def test_api_rejects_empty_query_and_unknown_scenario(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)

    empty = client.post("/v1/missions", json={"query": "  ", "mode": "read_only"})
    assert empty.status_code == 400
    assert "non-empty" in empty.json()["detail"]

    bad = client.post(
        "/v1/missions",
        json={
            "query": "Why has CAC increased?",
            "mode": "read_only",
            "scope": {"timezone": "Asia/Kolkata"},
            "scenario_id": "does_not_exist_xyz",
        },
    )
    assert bad.status_code == 400
    assert "Unknown scenario_id" in bad.json()["detail"]

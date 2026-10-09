"""V3 mission visibility in the Office UI's read path — the connectivity
gap found and fixed 2026-09-18 (docs/refactor/TASK_SHEET.md log)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from seleric_swarm.analytics.chart_vocabulary import CHART_TYPES
from seleric_swarm.api.office import registry
from seleric_swarm.api.v3_state import get_v3_artifact_store, get_v3_mission_store
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.state.missions import Mission


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    from seleric_swarm.api.office import gateway

    class _EmptyStore:
        def get_raw(self, mission_id):
            return None

        def get(self, mission_id):
            return None

    class _Runtime:
        store = _EmptyStore()

    monkeypatch.setattr(gateway, "_runtime", lambda: _Runtime())
    registry.clear()

    from seleric_swarm import main

    return TestClient(main.app)


def _seed_v3_mission(mission_id: str, *, status: str = "completed") -> None:
    mission_store = get_v3_mission_store()
    mission_store.create(
        Mission(
            mission_id=mission_id,
            query="what were net sales yesterday?",
            as_of=datetime.now(UTC),
            workspace_id="default",
            owner_user_id="default",
            thread_id="thread-1",
            run_id="run-1",
        )
    )
    mission_store.finish(mission_id, status=status, final_response="net sales: 123", error_code=None)
    registry.register_mission(mission_id)
    artifact_store = get_v3_artifact_store()
    artifact_store.put(
        Artifact(
            workspace_id="default",
            artifact_type="evidence",
            payload={"metric_id": "metric.net_sales"},
            classification="factual",
            evidence_ids=["ev-source"],
            provenance=ArtifactProvenance(evidence_ids=["ev-source"]),
            mission_id=mission_id,
        )
    )


def test_v3_mission_appears_in_office_list(client: TestClient) -> None:
    _seed_v3_mission("MS3-office-1")
    body = client.get("/v1/office/missions").json()
    ids = [m["missionId"] for m in body["missions"]]
    assert "MS3-office-1" in ids


def test_v3_mission_snapshot_renders(client: TestClient) -> None:
    _seed_v3_mission("MS3-office-2")
    body = client.get("/v1/office/missions/MS3-office-2/snapshot").json()
    assert body["missionId"] == "MS3-office-2"
    assert body["status"] == "completed"
    assert body["stage"] == "complete"
    assert body["finalResponse"] == "net sales: 123"
    assert body["artifacts"]["evidence"] == 1
    assert body["trace"] == {"requestId": "run-1", "sessionId": "thread-1"}
    coordinator = next(a for a in body["agents"] if a["agentId"] == "coordinator")
    assert coordinator["missionLead"] is True


def test_unknown_mission_id_still_404s(client: TestClient) -> None:
    resp = client.get("/v1/office/missions/does-not-exist/snapshot")
    assert resp.status_code == 404


def test_office_chart_types_endpoint_publishes_the_vocabulary(client):
    """The UI validates specs against this payload, so every declared form must
    be published and the shape must match what the renderer consumes."""
    response = client.get("/v1/office/chart-types")
    assert response.status_code == 200
    payload = response.json()

    assert payload["default_chart_type"] == "bar"
    published = [entry["chart_type"] for entry in payload["chart_types"]]
    assert published == list(CHART_TYPES)
    for entry in payload["chart_types"]:
        assert entry["label"], entry
        assert entry["description"], entry
        assert entry["stacking"] in {"none", "total"}, entry
        assert isinstance(entry["aliases"], list), entry


def test_office_chart_types_endpoint_does_not_require_a_mission(client):
    response = client.get("/v1/office/chart-types")
    assert response.status_code == 200


def _chart_spec_artifact(mission_id: str, chart_type: str = "stacked bar") -> str:
    """A chart_spec for two periods, the shape that used to come back as a line."""
    from seleric_swarm.agent.artifacts import EvidenceArtifact
    from seleric_swarm.analytics.visualization import (
        describe_evidence_shape,
        generate_visualization_spec,
        infer_chart_type_from_shape,
    )

    evidence = [
        EvidenceArtifact(
            metric_id="metric.net_profit",
            as_of=datetime.now(UTC),
            period_start=datetime(2026, 10, day, tzinfo=UTC),
            period_end=datetime(2026, 10, day, 23, 59, tzinfo=UTC),
            value=value,
            grain="day",  # type: ignore[arg-type]
            unit="inr",
            dimensions={},
            source_query={},
        )
        for day, value in ((2, 100.0), (3, 200.0))
    ]
    shape = describe_evidence_shape(evidence)
    # The structural default is still a line; only the explicit form changes it.
    assert infer_chart_type_from_shape(shape) == "line"
    spec = generate_visualization_spec(evidence, "net profit waterfall", "Net profit", chart_type=chart_type)
    assert spec["chart_type"] == "stacked_bar"

    return get_v3_artifact_store().put(
        Artifact(
            workspace_id="default",
            artifact_type="chart_spec",
            payload=spec,
            classification="derived",
            evidence_ids=["e1"],
            provenance=ArtifactProvenance(calculation_version="v1"),
            mission_id=mission_id,
        )
    ).id


def test_v3_chart_payload_keeps_the_canonical_form() -> None:
    """The requested form must reach the UI intact, not be remapped in transit."""
    from seleric_swarm.api.office.v3_adapter import v3_raw_snapshot

    mission_id = "mission-chart-types"
    _seed_v3_mission(mission_id)
    artifact_id = _chart_spec_artifact(mission_id)

    charts = v3_raw_snapshot(mission_id)["charts"]
    emitted = next(chart for chart in charts if chart["artifact_id"] == artifact_id)
    assert emitted["chart_type"] == "stacked_bar"
    assert emitted["data"]["chart_type"] == "stacked_bar"
    assert all(series["type"] == "bar" for series in emitted["data"]["series"])


def test_v3_chart_without_a_type_is_bridged_as_bar_and_logged(caplog) -> None:
    """A payload that lost its form still renders, but says so instead of hiding it."""
    import logging

    from seleric_swarm.api.office.v3_adapter import v3_raw_snapshot

    mission_id = "mission-chart-no-type"
    _seed_v3_mission(mission_id)
    store = get_v3_artifact_store()
    artifact_id = store.put(
        Artifact(
            workspace_id="default",
            artifact_type="chart_spec",
            payload={"title": "Legacy", "series": [], "data": []},
            classification="derived",
            evidence_ids=["e1"],
            provenance=ArtifactProvenance(calculation_version="v1"),
            mission_id=mission_id,
        )
    ).id

    with caplog.at_level(logging.WARNING, logger="seleric.api.office.v3"):
        charts = v3_raw_snapshot(mission_id)["charts"]
    emitted = next(chart for chart in charts if chart["artifact_id"] == artifact_id)
    assert emitted["chart_type"] == "bar"
    assert artifact_id in caplog.text


def test_chart_vocabulary_endpoint_matches_the_frontend_mirror(client) -> None:
    """Drift guard: office-ui/src/api/chartContract.ts mirrors this vocabulary.

    The UI validates every incoming spec against the list it ships locally, so
    the two lists must stay identical. Read the mirror out of the TS source and
    compare, so this test fails the moment one side moves.
    """
    from pathlib import Path

    ui_file = Path(__file__).resolve().parents[2] / "office-ui" / "src" / "api" / "chartContract.ts"
    assert ui_file.exists(), f"frontend chart contract not found at {ui_file}"

    source = ui_file.read_text(encoding="utf-8")
    declared = re.search(r"export const CHART_TYPES = \[(?P<body>.*?)\] as const;", source, re.DOTALL)
    assert declared, "CHART_TYPES not found in the frontend chart contract"
    local_forms = re.findall(r'"([a-z_]+)"', declared.group("body"))
    assert local_forms, "no chart forms found in the frontend chart contract"

    published = [entry["chart_type"] for entry in client.get("/v1/office/chart-types").json()["chart_types"]]
    assert sorted(local_forms) == sorted(published), (
        "the frontend mirror and the backend vocabulary disagree — update both:\n"
        f"  frontend: {sorted(local_forms)}\n  backend:  {sorted(published)}"
    )

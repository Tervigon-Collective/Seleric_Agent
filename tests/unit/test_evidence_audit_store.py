"""``PostgresMissionStore`` must persist evidence DIMENSIONS, not its time range.

Live 2026-10-06 (MS3-167d9f4838): ``evidence_artifacts.dimensions`` was written
from ``EvidenceView.time_range``, so all 47 rows of a mission persisted as
``{"start": ..., "end": ...}`` and the real grouping (``campaign_name`` and
siblings) was discarded. The audit store could not say which campaign a value
belonged to, which is precisely what an evidence store exists to answer.

The two fields are both ``dict[str, str]`` structurally, so swapping them is
invisible to a checker — they are distinct ``NewType``s on ``EvidenceView`` now
so a repeat is a type error. This test pins the *behaviour*, since the types
alone would not catch a ``_json(evidence.<wrong field>)`` edit.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.contracts.lookup import EvidenceView, MissionResult, TraceInfo
from seleric_swarm.persistence.postgres import PostgresMissionStore


class _WriteResult:
    """``rowcount`` must be non-zero: put() early-returns on a lost CAS."""

    rowcount = 1


class _RecordingConn:
    """Captures every ``execute(sql, params)`` so the bound values can be read."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _WriteResult:
        self.calls.append((str(statement), dict(params or {})))
        return _WriteResult()


class _RecordingEngine:
    def __init__(self) -> None:
        self.conn = _RecordingConn()

    def begin(self) -> Any:
        conn = self.conn

        class _Ctx:
            def __enter__(self_inner) -> _RecordingConn:
                return conn

            def __exit__(self_inner, *exc: Any) -> None:
                return None

        return _Ctx()


def _evidence_insert_params(engine: _RecordingEngine) -> dict[str, Any]:
    for sql, params in engine.conn.calls:
        if "INSERT INTO evidence_artifacts" in sql:
            return params
    raise AssertionError("no evidence_artifacts insert was issued")


def test_evidence_dimensions_column_receives_dimensions_not_the_time_range() -> None:
    engine = _RecordingEngine()
    store = PostgresMissionStore(engine)  # type: ignore[arg-type]
    result = MissionResult(
        mission_id="MS3-audit",
        status="completed",
        trace=TraceInfo(request_id="req-1", session_id="sess-1"),
        evidence=[
            EvidenceView(
                evidence_id="artifact_1",
                metric_or_fact="ad_spend",
                value=28883.19,
                unit="INR",
                source="seleric-mcp",
                time_range={"start": "2026-10-03", "end": "2026-10-05"},
                dimensions={"campaign_name": "PMax - Seasonal New"},
                provenance={"artifact_id": "artifact_1"},
            )
        ],
    )

    store.put(result)

    params = _evidence_insert_params(engine)
    import json

    assert json.loads(params["dimensions"]) == {"campaign_name": "PMax - Seasonal New"}, (
        "the dimensions column must hold the row's grouping, not its period — "
        "writing time_range here loses every breakdown the mission fetched"
    )
    # The period itself is still reachable: it is rebuilt from the artifact's
    # period_start/period_end on read (api/office/v3_adapter.py::_evidence_rows),
    # so dropping it here loses nothing.
    assert json.loads(params["value_json"]) == 28883.19
    assert params["metric_or_fact"] == "ad_spend"
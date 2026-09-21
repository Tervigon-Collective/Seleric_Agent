"""``_to_lookup`` must surface the already-computed intent as ``query_class``.

Live incident (2026-09-21): every V3 mission's API response showed
``"query_class": null`` regardless of outcome. ``run_v3_mission`` already
calls ``classify_intent()`` and stores the result at ``result.trace["intent"]``
(also used for the alias fast-path and included in every V3MissionResult's
trace dict), but ``_to_lookup`` never copied it onto the ``query_class``
field of the ``LookupMissionResult`` the API/UI actually reads.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agent.output import MissionResult as V3MissionResult
from seleric_swarm.agent.runner import _to_lookup


def _v3_result(*, intent: str | None) -> V3MissionResult:
    return V3MissionResult(
        mission_id="mission-1",
        status="completed",
        query="net sales last month",
        as_of=datetime(2026, 9, 21, tzinfo=UTC),
        final_response="answer",
        trace={"request_id": "req-1", "session_id": "sess-1", "intent": intent},
    )


def test_query_class_is_populated_from_the_trace_intent():
    lookup = _to_lookup(
        _v3_result(intent="lookup"), request_id="req-1", session_id="sess-1", evidence=[]
    )
    assert lookup.query_class == "lookup"


def test_query_class_stays_none_when_intent_classification_was_unavailable():
    lookup = _to_lookup(
        _v3_result(intent=None), request_id="req-1", session_id="sess-1", evidence=[]
    )
    assert lookup.query_class is None

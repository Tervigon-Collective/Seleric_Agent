from __future__ import annotations

import pytest
from pydantic import ValidationError

from seleric_swarm.api.mission_access import can_access_mission
from seleric_swarm.conversations import Principal, PrincipalAuthMethod
from seleric_swarm.swarm.envelope import (
    Intent,
    a2a_json_schema,
    validate_a2a_payload,
)


def _principal(user_id: str) -> Principal:
    return Principal(
        principal_id=user_id,
        workspace_id="workspace_1",
        user_id=user_id,
        authenticated=True,
        auth_method=PrincipalAuthMethod.SERVICE,
    )


def test_mission_access_enforces_owner_and_hides_unowned_legacy_rows():
    owned = {"workspace_id": "workspace_1", "owner_user_id": "user_1"}
    assert can_access_mission(_principal("user_1"), owned) is True
    assert can_access_mission(_principal("user_2"), owned) is False
    assert can_access_mission(_principal("user_1"), {}) is False
    assert (
        can_access_mission(
            _principal("user_1"),
            {},
            default_workspace_id="workspace_1",
            default_user_id="user_1",
        )
        is True
    )


def test_a2a_schema_and_validation_share_the_python_contract():
    schema = a2a_json_schema()
    intent_values = schema["$defs"]["Intent"]["enum"]
    assert Intent.HANDOFF_PROPOSAL.value in intent_values
    assert schema["properties"]["protocol"]["default"] == "seleric.swarm.v1"

    message = validate_a2a_payload(
        {
            "mission_id": "mission_1",
            "from_agent": "coordinator",
            "to_agent": "diagnostic",
            "intent": "task_request",
        }
    )
    assert message.protocol == "seleric.swarm.v1"
    with pytest.raises(ValidationError):
        validate_a2a_payload(
            {
                "mission_id": "mission_1",
                "from_agent": "coordinator",
                "intent": "not-a-real-intent",
            }
        )

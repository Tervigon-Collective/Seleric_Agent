from __future__ import annotations

from seleric_swarm.api.mission_access import can_access_mission
from seleric_swarm.conversations import Principal, PrincipalAuthMethod


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

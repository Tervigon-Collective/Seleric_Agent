"""Voice worker identity parsing — no LiveKit dependency required.

``principal_from_metadata`` is the worker's only trust boundary: everything it
returns is used to act as a real user against the conversation API. A partial
or malformed payload must produce ``None``, never a default-principal fallback
(blocker B6 in docs/features/voice-agent/02_PHASED_PLAN.md).
"""

from __future__ import annotations

import json

import pytest

from seleric_swarm.voice.worker import VoicePrincipal, principal_from_metadata


def test_parses_a_complete_payload() -> None:
    raw = json.dumps(
        {"workspace_id": "ws_1", "user_id": "user_1", "thread_id": "thr_1"}
    )

    assert principal_from_metadata(raw) == VoicePrincipal(
        workspace_id="ws_1", user_id="user_1", thread_id="thr_1"
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "not json",
        "[]",
        '"a string"',
        json.dumps({"workspace_id": "ws_1", "user_id": "user_1"}),  # no thread
        json.dumps({"workspace_id": "ws_1", "thread_id": "thr_1"}),  # no user
        json.dumps({"user_id": "user_1", "thread_id": "thr_1"}),  # no workspace
        json.dumps({"workspace_id": " ", "user_id": "u", "thread_id": "t"}),
    ],
)
def test_refuses_anything_incomplete(raw: str | None) -> None:
    assert principal_from_metadata(raw) is None


def test_extra_fields_are_ignored_not_trusted() -> None:
    raw = json.dumps(
        {
            "workspace_id": "ws_1",
            "user_id": "user_1",
            "thread_id": "thr_1",
            "roles": ["admin"],
            "authenticated": True,
        }
    )

    principal = principal_from_metadata(raw)

    assert principal is not None
    assert not hasattr(principal, "roles")

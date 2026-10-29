"""The email preview is an APPROVAL part a tool adds to the turn; it must survive into the stored assistant message."""

from __future__ import annotations

from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.conversations.contracts import MessagePartType

PREVIEW = {"approval_id": "draft_1", "action": "send_email", "version": 2, "subject": "Hello",
           "to": ["gaurav@seleric.ai"], "status": "draft"}


def test_email_preview_part_reaches_the_stored_message() -> None:
    parts = conversations_api._answer_parts("Here is the draft.", {"parts": [{"type": "APPROVAL", "content": PREVIEW}]})
    (card,) = [p for p in parts if p.type is MessagePartType.APPROVAL]
    assert card.content["approval_id"] == "draft_1" and card.content["action"] == "send_email"


def test_an_approval_part_without_an_id_is_dropped_not_stored() -> None:
    parts = conversations_api._answer_parts("x", {"parts": [{"type": "APPROVAL", "content": {"action": "send_email"}}]})
    assert not [p for p in parts if p.type is MessagePartType.APPROVAL]

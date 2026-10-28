"""Voice context projection — no LiveKit dependency required."""

from __future__ import annotations

import pytest

from seleric_swarm.voice.projection import (
    MAX_DURABLE,
    MAX_TURNS,
    VoiceProjection,
    fetch_voice_projection,
    project_voice_context,
)


def _msg(role: str, text: str, *, mid: str = "m") -> dict:
    return {
        "id": mid,
        "role": role,
        "parts": [{"type": "TEXT", "content": text}],
    }


def test_projects_recent_turns_and_skips_working_placeholder() -> None:
    messages = [
        _msg("USER", "How were sales last week?", mid="1"),
        _msg("ASSISTANT", "Working…", mid="2"),
        _msg("ASSISTANT", "Sales were up 12% week over week.", mid="3"),
        _msg("USER", "And returns?", mid="4"),
        _msg("ASSISTANT", "Returns stayed flat.", mid="5"),
    ]
    projection = project_voice_context(messages=messages)
    assert projection.turns == (
        ("user", "How were sales last week?"),
        ("assistant", "Sales were up 12% week over week."),
        ("user", "And returns?"),
        ("assistant", "Returns stayed flat."),
    )
    assert not projection.empty


def test_keeps_only_the_most_recent_turn_pairs() -> None:
    messages = []
    for i in range(12):
        messages.append(_msg("USER", f"question {i}", mid=f"u{i}"))
        messages.append(_msg("ASSISTANT", f"answer {i}", mid=f"a{i}"))
    projection = project_voice_context(messages=messages)
    assert len(projection.turns) == 2 * MAX_TURNS
    assert projection.turns[0] == ("user", f"question {12 - MAX_TURNS}")
    assert projection.turns[-1] == ("assistant", "answer 11")


def test_durable_notes_are_filtered_and_capped() -> None:
    memories = [
        {"id": "1", "type": "PREFERENCE", "status": "ACTIVE", "content": "I prefer weekly reporting"},
        {"id": "2", "type": "FACT", "status": "ACTIVE", "content": "random fact should be skipped"},
        {"id": "3", "type": "DECISION", "status": "ACTIVE", "content": "We decided to use net revenue"},
        {"id": "4", "type": "CONSTRAINT", "status": "PENDING_CONSENT", "content": "must never ship"},
        {"id": "5", "type": "DEFINITION", "status": "ACTIVE", "content": "Revenue means net revenue"},
        {"id": "6", "type": "PREFERENCE", "status": "ACTIVE", "content": "Keep answers short"},
        {"id": "7", "type": "DECISION", "status": "ACTIVE", "content": "Use IST calendar"},
        {"id": "8", "type": "CONSTRAINT", "status": "ACTIVE", "content": "Never invent numbers"},
        {"id": "9", "type": "PREFERENCE", "status": "ACTIVE", "content": "Extra past the durable cap"},
    ]
    projection = project_voice_context(memories=memories)
    assert len(projection.durable_notes) == MAX_DURABLE
    assert all(
        note.startswith(("preference:", "decision:", "constraint:", "definition:"))
        for note in projection.durable_notes
    )
    assert "random fact" not in " ".join(projection.durable_notes)
    assert "must never ship" not in " ".join(projection.durable_notes)
    assert projection.composition["durable_count"] == MAX_DURABLE
    assert "1" in projection.composition["memory_ids"]


def test_instructions_suffix_and_augment() -> None:
    projection = VoiceProjection(
        durable_notes=("preference: weekly reporting",),
        composition={},
    )
    suffix = projection.instructions_suffix()
    assert "weekly reporting" in suffix
    assert "never invent numbers" in suffix.casefold() or "never invent" in suffix.casefold()
    assert projection.augment_instructions("Base.").startswith("Base.")
    assert "weekly reporting" in projection.augment_instructions("Base.")


def test_empty_projection_has_no_suffix() -> None:
    projection = VoiceProjection()
    assert projection.empty
    assert projection.instructions_suffix() == ""
    assert projection.augment_instructions("Base.") == "Base."


@pytest.mark.asyncio
async def test_fetch_voice_projection_fail_open() -> None:
    class Boom:
        async def get(self, *args, **kwargs):
            raise RuntimeError("down")

    projection = await fetch_voice_projection(
        Boom(),
        api_url="http://x",
        headers={},
        thread_id="thr",
    )
    assert projection.empty


@pytest.mark.asyncio
async def test_fetch_voice_projection_loads_api_payloads() -> None:
    class Client:
        async def get(self, url, headers=None, params=None):
            class Res:
                is_success = True

                def json(self_inner):
                    if "messages" in url:
                        return [
                            _msg("USER", "Prior typed question", mid="1"),
                            _msg("ASSISTANT", "Prior typed answer", mid="2"),
                        ]
                    return [
                        {
                            "id": "mem1",
                            "type": "PREFERENCE",
                            "status": "ACTIVE",
                            "content": "Prefer INR",
                        }
                    ]

            return Res()

    projection = await fetch_voice_projection(
        Client(),
        api_url="http://api",
        headers={"x-api-key": "k"},
        thread_id="thr_1",
    )
    assert projection.turns[0][1] == "Prior typed question"
    assert projection.durable_notes == ("preference: Prefer INR",)

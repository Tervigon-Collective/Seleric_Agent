"""SSE streaming branch of POST /v1/missions (stream=true)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import seleric_swarm.main as main
from seleric_swarm.main import MissionRequest, _sse_frame, _stream_mission_response


def _as_text(chunk: object) -> str:
    return chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk)


def _frames(raw: str) -> list[dict]:
    out: list[dict] = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: "):]))
    return out


def test_sse_frame_shape() -> None:
    frame = _sse_frame("answer.delta", {"delta": "hi"}, 3)
    assert "id: 3\n" in frame
    assert "event: answer.delta\n" in frame
    data = json.loads(frame.split("data: ", 1)[1].strip())
    assert data == {"delta": "hi", "type": "answer.delta"}


@pytest.mark.asyncio
async def test_stream_emits_deltas_then_completed(monkeypatch) -> None:
    async def fake_run(runtime, *, on_stream=None, **kwargs):
        assert on_stream is not None
        on_stream("delta", "You got ")
        on_stream("delta", "28 orders.")
        return {
            "route": "v3",
            "result": {
                "mission_id": "MS3-x",
                "status": "completed",
                "final_response": "You got 28 orders.",
                "evidence": [],
                "limitations": [],
            },
        }

    monkeypatch.setattr(main, "run_v3_mission", fake_run)

    response = _stream_mission_response(
        SimpleNamespace(),
        query="how many orders",
        timezone="Asia/Kolkata",
        as_of=None,
        session_id="s1",
        request_id="r1",
        principal=SimpleNamespace(workspace_id="ws", user_id="u"),
        req=MissionRequest(query="how many orders", stream=True),
    )

    raw = "".join([_as_text(chunk) async for chunk in response.body_iterator])
    frames = _frames(raw)
    types = [f["type"] for f in frames]

    assert types == ["answer.started", "answer.delta", "answer.delta", "answer.completed"]
    assert "".join(f["delta"] for f in frames if f["type"] == "answer.delta") == "You got 28 orders."
    assert frames[-1]["final_response"] == "You got 28 orders."
    assert frames[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_stream_surfaces_error_frame(monkeypatch) -> None:
    async def boom(runtime, *, on_stream=None, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(main, "run_v3_mission", boom)

    response = _stream_mission_response(
        SimpleNamespace(),
        query="q",
        timezone="Asia/Kolkata",
        as_of=None,
        session_id="s1",
        request_id="r1",
        principal=SimpleNamespace(workspace_id="ws", user_id="u"),
        req=MissionRequest(query="q", stream=True),
    )

    frames = _frames("".join([_as_text(chunk) async for chunk in response.body_iterator]))
    assert frames[-1]["type"] == "error"
    assert "kaboom" in frames[-1]["error"]

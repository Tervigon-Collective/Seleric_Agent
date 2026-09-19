from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.api.v3_state import get_v3_artifact_store, get_v3_mission_store, reset_v3_stores
from seleric_swarm.contracts.lookup import MissionResult, TraceInfo
from seleric_swarm.conversations.contracts import (
    Message,
    MessagePart,
    MessagePartType,
    MessageRole,
    Thread,
)
from seleric_swarm.conversations.file_store import build_file_repositories
from seleric_swarm.conversations.postgres import build_conversation_repositories
from seleric_swarm.persistence.file_store import FileMissionStore, file_paths
from seleric_swarm.state.missions import Mission


def test_file_conversations_survive_rebuild(tmp_path) -> None:
    root = tmp_path / "state"
    first = build_file_repositories(root)
    thread = first.threads.create(
        Thread(workspace_id="workspace_1", owner_user_id="user_1", title="ns then np")
    )
    first.messages.create(
        Message(
            thread_id=thread.id,
            workspace_id="workspace_1",
            user_id="user_1",
            role=MessageRole.USER,
            parts=[MessagePart(type=MessagePartType.TEXT, content="ns")],
        )
    )
    first.messages.create(
        Message(
            thread_id=thread.id,
            workspace_id="workspace_1",
            role=MessageRole.ASSISTANT,
            parts=[MessagePart(type=MessagePartType.TEXT, content="sales: 71,727 INR")],
        )
    )

    restarted = build_conversation_repositories("file", "", persist_path=str(root))
    loaded = restarted.threads.get(thread.id, "workspace_1", "user_1")
    assert loaded is not None
    assert loaded.title == "ns then np"
    texts = [
        part.content
        for message in restarted.messages.list_for_thread(thread.id)
        for part in message.parts
    ]
    assert "ns" in texts
    assert "sales: 71,727 INR" in texts


def test_file_mission_store_survives_rebuild(tmp_path) -> None:
    path = file_paths(tmp_path / "state").missions
    store = FileMissionStore(path)
    store.put(
        MissionResult(
            mission_id="MS3-keep",
            status="completed",
            final_response="kept",
            trace=TraceInfo(request_id="req", session_id="thread"),
        ),
        {"route": "v3", "final_response": "kept", "query": "ns"},
    )
    restarted = FileMissionStore(path)
    raw = restarted.get_raw("MS3-keep")
    assert raw is not None
    assert raw["final_response"] == "kept"
    assert restarted.get("MS3-keep") is not None


def test_file_v3_stores_survive_rebuild(tmp_path) -> None:
    from seleric_swarm.api.v3_state import configure_v3_persistence

    configure_v3_persistence(tmp_path / "state")
    try:
        mission = get_v3_mission_store().create(
            Mission(
                mission_id="MS3-disk",
                query="np",
                as_of=datetime.now(UTC),
                workspace_id="default",
                owner_user_id="default",
            )
        )
        get_v3_mission_store().finish(
            mission.mission_id,
            status="completed",
            final_response="profit: 1",
        )
        configure_v3_persistence(tmp_path / "state")
        loaded = get_v3_mission_store().get("MS3-disk")
        assert loaded is not None
        assert loaded.final_response == "profit: 1"
        assert get_v3_artifact_store().list_for_mission("MS3-disk") == []
    finally:
        reset_v3_stores()

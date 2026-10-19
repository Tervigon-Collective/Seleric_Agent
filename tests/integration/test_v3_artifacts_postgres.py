"""Postgres-backed V3 artifact store against a real database (SELERIC_TEST_DATABASE_URL)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from seleric_swarm.conversations.contracts import Artifact
from seleric_swarm.persistence.migrate import run_migrations
from seleric_swarm.persistence.v3_artifacts import PostgresV3ArtifactStore


def _engine():
    pytest.importorskip("psycopg")
    url = os.getenv("SELERIC_TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("SELERIC_TEST_DATABASE_URL is not configured")
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    try:
        with create_engine(url, connect_args={"connect_timeout": 1}).connect() as connection:
            connection.execute(text("SELECT 1"))
    except (OperationalError, SQLAlchemyError) as exc:
        pytest.skip(f"PostgreSQL integration service unavailable: {exc}")
    run_migrations(url)
    return create_engine(url, pool_pre_ping=True)


def _artifact(mission: str, thread: str, kind: str = "evidence", **payload) -> Artifact:
    return Artifact(
        workspace_id="w",
        artifact_type=kind,
        payload=payload or {"value": 1.0},
        classification="factual",
        evidence_ids=["raw:x"],
        mission_id=mission,
        thread_id=thread,
    )


def test_artifacts_survive_the_process_and_are_found_by_thread():
    engine = _engine()
    mission, thread = f"MS3-{uuid4().hex[:8]}", f"thread-{uuid4().hex[:8]}"
    writer = PostgresV3ArtifactStore(engine)
    evidence = writer.put(_artifact(mission, thread, value=42.0))
    record = writer.put(_artifact(mission, thread, "turn_record", query="net sales", offer="By channel?"))
    assert writer.flush()

    # A different process (fresh store, empty cache) reads everything back.
    reader = PostgresV3ArtifactStore(engine)
    assert reader.get(evidence.id).payload == {"value": 42.0}
    assert [a.id for a in reader.get_many([record.id, evidence.id, "missing"])] == [record.id, evidence.id]
    assert {a.id for a in reader.list_for_mission(mission)} == {evidence.id, record.id}
    newest = reader.list_for_context("", thread)
    assert newest[0].id == record.id and newest[0].payload["offer"] == "By channel?"
    assert reader.list_for_context("other-workspace", thread) == []
    writer.close()
    reader.close()


def test_old_missions_leave_the_cache_only_after_they_are_written():
    engine = _engine()
    store = PostgresV3ArtifactStore(engine, cached_missions=2)
    thread = f"thread-{uuid4().hex[:8]}"
    first = store.put(_artifact(f"MS3-{uuid4().hex[:8]}", thread))
    assert store.flush()
    for _ in range(3):
        store.put(_artifact(f"MS3-{uuid4().hex[:8]}", thread))
    assert store.flush()
    assert first.id not in store._by_id  # evicted from memory...
    assert store.get(first.id) is not None  # ...still readable from Postgres
    store.close()


async def test_a_submitted_run_wakes_the_listening_worker():
    import asyncio
    import threading

    from seleric_swarm.recovery import DurablePollingRunQueue, listen_for_runs

    engine = _engine()
    url = engine.url.render_as_string(hide_password=False)
    wake, stop = asyncio.Event(), threading.Event()
    listen_for_runs(url, wake, asyncio.get_running_loop(), stop)
    try:
        await asyncio.sleep(0.5)  # let the listener connect and LISTEN
        await DurablePollingRunQueue(engine).enqueue("run-1")
        await asyncio.wait_for(wake.wait(), timeout=5)
    finally:
        stop.set()

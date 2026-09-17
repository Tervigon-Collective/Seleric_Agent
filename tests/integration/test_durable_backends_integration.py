from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from seleric_swarm.cancellation import RedisCancellationBackend
from seleric_swarm.checkpointing import PostgresCheckpointProvider
from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    EventVisibility,
    Run,
    RunAttempt,
    Thread,
)
from seleric_swarm.conversations.postgres import build_conversation_repositories
from seleric_swarm.persistence.migrate import run_migrations


def _postgres_url() -> str:
    pytest.importorskip("psycopg")
    url = os.getenv("SELERIC_TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("SELERIC_TEST_DATABASE_URL is not configured")
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _require_postgres(url: str) -> None:
    try:
        with create_engine(url, connect_args={"connect_timeout": 1}).connect() as connection:
            connection.execute(text("SELECT 1"))
    except (OperationalError, SQLAlchemyError) as exc:
        pytest.skip(f"PostgreSQL integration service unavailable: {exc}")


@pytest.mark.asyncio
async def test_postgres_checkpoint_pool_and_run_leases():
    url = _postgres_url()
    _require_postgres(url)

    run_migrations(url)
    repositories = build_conversation_repositories("postgres", url)
    thread = repositories.threads.create(
        Thread(workspace_id="integration", owner_user_id="integration")
    )
    run = repositories.runs.create(
        Run(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            requested_by_user_id=thread.owner_user_id,
            current_attempt=1,
        )
    )
    attempt = repositories.runs.add_attempt(
        RunAttempt(run_id=run.id, attempt_number=1)
    )
    claimed = repositories.runs.claim(
        run.id, "integration-worker", 10, now=datetime.now(UTC)
    )
    assert claimed is not None
    assert repositories.runs.heartbeat(
        attempt.id,
        "integration-worker",
        10,
        expected_version=claimed.version,
    )

    provider = PostgresCheckpointProvider(url)
    await provider.setup()
    assert provider.get_checkpointer() is not None
    await provider.close()


def test_postgres_activity_event_survives_repository_restart():
    url = _postgres_url()
    _require_postgres(url)
    applied = run_migrations(url)
    assert all(name.endswith(".sql") for name in applied)

    suffix = uuid4().hex
    repositories = build_conversation_repositories("postgres", url)
    thread = repositories.threads.create(
        Thread(
            id=f"thread_integration_{suffix}",
            workspace_id="integration",
            owner_user_id="integration",
        )
    )
    run = repositories.runs.create(
        Run(
            id=f"run_integration_{suffix}",
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            requested_by_user_id=thread.owner_user_id,
        )
    )
    appended = repositories.runs.append_event(
        ActivityEvent(
            id=f"event_integration_{suffix}",
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            run_id=run.id,
            owner_user_id=thread.owner_user_id,
            actor_user_id=thread.owner_user_id,
            actor_type="agent",
            actor_id="diagnostic",
            parent_event_id="event_parent",
            event_type="evidence.added",
            evidence_ids=["EV-integration"],
            metadata={"source": "integration"},
            visibility=EventVisibility.USER,
            started_at=datetime.now(UTC),
        )
    )

    restarted = build_conversation_repositories("postgres", url)
    assert restarted.threads.get(thread.id) == thread
    loaded = restarted.runs.list_events(run.id)
    assert loaded == [appended]
    assert loaded[0].thread_sequence > 0
    assert loaded[0].evidence_ids == ["EV-integration"]


def test_redis_cancellation_round_trip():
    url = os.getenv("SELERIC_TEST_REDIS_URL", "").strip()
    if not url:
        pytest.skip("SELERIC_TEST_REDIS_URL is not configured")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    try:
        client.ping()
        backend = RedisCancellationBackend(client, prefix="seleric:test:cancel:")
        mission_id = "integration-durable-runtime"
        backend.request(mission_id)
        assert backend.is_requested(mission_id)
        backend.clear(mission_id)
        assert not backend.is_requested(mission_id)
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        pytest.skip(f"Redis integration service unavailable: {exc}")

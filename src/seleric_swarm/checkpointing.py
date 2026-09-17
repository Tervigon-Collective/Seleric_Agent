"""LangGraph checkpoint provider ports and dependency-light adapters."""

from __future__ import annotations

from typing import Any, Protocol


class CheckpointProvider(Protocol):
    """Supplies a process-local or durable LangGraph checkpointer."""

    def get_checkpointer(self) -> Any | None: ...

    def config(self, *, thread_id: str, run_id: str | None = None) -> dict[str, Any]: ...

    def close(self) -> None: ...


class NoOpCheckpointProvider:
    def get_checkpointer(self) -> None:
        return None

    def config(self, *, thread_id: str, run_id: str | None = None) -> dict[str, Any]:
        return {}

    def close(self) -> None:
        return None


class InMemoryCheckpointProvider:
    def __init__(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        self._checkpointer = InMemorySaver()

    def get_checkpointer(self) -> Any:
        return self._checkpointer

    def config(self, *, thread_id: str, run_id: str | None = None) -> dict[str, Any]:
        configurable = {"thread_id": thread_id}
        if run_id:
            configurable["checkpoint_ns"] = run_id
        return {"configurable": configurable}

    def close(self) -> None:
        return None


class PostgresCheckpointProvider:
    """Process-safe LangGraph checkpointer backed by the audit database."""

    def __init__(self, database_url: str, *, setup: bool = True) -> None:
        if not database_url.strip():
            raise ValueError("checkpoint_backend=postgres requires database_url")
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection

        self._connection = Connection.connect(
            database_url,
            autocommit=True,
            prepare_threshold=0,
        )
        self._checkpointer = PostgresSaver(self._connection)
        if setup:
            self._checkpointer.setup()

    def get_checkpointer(self) -> Any:
        return self._checkpointer

    def config(self, *, thread_id: str, run_id: str | None = None) -> dict[str, Any]:
        configurable = {"thread_id": thread_id}
        if run_id:
            configurable["checkpoint_ns"] = run_id
        return {"configurable": configurable}

    def close(self) -> None:
        self._connection.close()


def build_checkpoint_provider(backend: str, database_url: str = "") -> CheckpointProvider:
    if backend == "memory":
        return InMemoryCheckpointProvider()
    if backend == "postgres":
        return PostgresCheckpointProvider(database_url)
    return NoOpCheckpointProvider()

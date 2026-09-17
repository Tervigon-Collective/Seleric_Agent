"""LangGraph checkpoint provider ports and dependency-light adapters."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, cast


class CheckpointProvider(Protocol):
    """Supplies a process-local or durable LangGraph checkpointer."""

    def get_checkpointer(self) -> Any | None: ...

    def config(self, *, thread_id: str, run_id: str | None = None) -> dict[str, Any]: ...

    async def setup(self) -> None: ...

    async def close(self) -> None: ...


class NoOpCheckpointProvider:
    def get_checkpointer(self) -> None:
        return None

    def config(self, *, thread_id: str, run_id: str | None = None) -> dict[str, Any]:
        return {}

    async def setup(self) -> None:
        return None

    async def close(self) -> None:
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

    async def setup(self) -> None:
        return None

    async def close(self) -> None:
        return None


class PostgresCheckpointProvider:
    """Async LangGraph checkpointer backed by a concurrency-safe connection pool."""

    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        if not database_url.strip():
            raise ValueError("checkpoint_backend=postgres requires database_url")
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        pool_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self._pool = AsyncConnectionPool(
            pool_url,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        self._checkpointer = AsyncPostgresSaver(cast(Any, self._pool))
        self._setup_lock = asyncio.Lock()
        self._ready = False

    def get_checkpointer(self) -> Any:
        return self._checkpointer

    def config(self, *, thread_id: str, run_id: str | None = None) -> dict[str, Any]:
        configurable = {"thread_id": thread_id}
        if run_id:
            configurable["checkpoint_ns"] = run_id
        return {"configurable": configurable}

    async def setup(self) -> None:
        if self._ready:
            return
        async with self._setup_lock:
            if self._ready:
                return
            await self._pool.open(wait=True)
            try:
                await self._checkpointer.setup()
            except BaseException:
                await self._pool.close()
                raise
            self._ready = True

    async def close(self) -> None:
        if self._ready:
            await self._pool.close()
            self._ready = False


def build_checkpoint_provider(backend: str, database_url: str = "") -> CheckpointProvider:
    if backend == "memory":
        return InMemoryCheckpointProvider()
    if backend == "postgres":
        return PostgresCheckpointProvider(database_url)
    return NoOpCheckpointProvider()

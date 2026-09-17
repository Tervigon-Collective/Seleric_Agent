"""Parallel execution helpers for independent ready tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def run_parallel(
    coros: list[Awaitable[T]],
    *,
    max_parallel: int = 4,
) -> list[T]:
    """Run awaitables with a concurrency cap, preserving input order."""
    if not coros:
        return []
    semaphore = asyncio.Semaphore(max(1, max_parallel))

    async def _wrap(coro: Awaitable[T]) -> T:
        async with semaphore:
            return await coro

    tasks = [asyncio.create_task(_wrap(coro)) for coro in coros]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def map_parallel(
    items: list[T],
    fn: Callable[[T], Awaitable[T]],
    *,
    max_parallel: int = 4,
) -> list[T]:
    return await run_parallel([fn(item) for item in items], max_parallel=max_parallel)

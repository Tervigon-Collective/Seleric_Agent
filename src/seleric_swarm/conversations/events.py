"""Append-only activity event sink and optional delivery notification ports."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, TypedDict, Unpack

from seleric_swarm.conversations.contracts import ActivityEvent, EventVisibility, Run
from seleric_swarm.conversations.event_mapping_v1 import map_mission_event
from seleric_swarm.conversations.repositories import RunRepository


class EventNotifier(Protocol):
    """Best-effort wake-up signal; persisted events remain the source of truth."""

    def publish(self, run_id: str, sequence: int) -> None: ...

    async def wait(self, run_id: str, after_sequence: int, timeout: float) -> None: ...


class PollingEventNotifier:
    """Dependency-free fallback suitable for one or many API workers."""

    def publish(self, run_id: str, sequence: int) -> None:
        return None

    async def wait(self, run_id: str, after_sequence: int, timeout: float) -> None:
        await asyncio.sleep(timeout)


class InMemoryEventNotifier:
    """Low-latency in-process notifier with polling-safe semantics."""

    def __init__(self) -> None:
        self._versions: dict[str, int] = {}
        self._conditions: dict[tuple[str, asyncio.AbstractEventLoop], asyncio.Condition] = {}

    def publish(self, run_id: str, sequence: int) -> None:
        self._versions[run_id] = max(sequence, self._versions.get(run_id, 0))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        condition = self._conditions.get((run_id, loop))
        if condition is not None:
            loop.create_task(self._notify(condition))

    @staticmethod
    async def _notify(condition: asyncio.Condition) -> None:
        async with condition:
            condition.notify_all()

    async def wait(self, run_id: str, after_sequence: int, timeout: float) -> None:
        if self._versions.get(run_id, 0) > after_sequence:
            return
        loop = asyncio.get_running_loop()
        condition = self._conditions.setdefault((run_id, loop), asyncio.Condition())
        if self._versions.get(run_id, 0) > after_sequence:
            return
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except TimeoutError:
            return


class RedisEventNotifier:
    """Adapter for an optional Redis publish/wait implementation."""

    def __init__(
        self,
        publish_callback: Callable[[str, int], None],
        wait_callback: Callable[[str, int, float], object],
    ) -> None:
        self._publish_callback = publish_callback
        self._wait_callback = wait_callback

    def publish(self, run_id: str, sequence: int) -> None:
        self._publish_callback(run_id, sequence)

    async def wait(self, run_id: str, after_sequence: int, timeout: float) -> None:
        result = self._wait_callback(run_id, after_sequence, timeout)
        if hasattr(result, "__await__"):
            await result


class ActivityEventFields(TypedDict, total=False):
    id: str
    actor_user_id: str | None
    sequence: int
    actor_type: str | None
    actor_id: str | None
    title: str | None
    summary: str | None
    parent_event_id: str | None
    visibility: EventVisibility
    evidence_ids: list[str]
    metadata: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    created_at: datetime


class ActivityEventSink:
    """Single append path for API lifecycle and future coordinator callbacks."""

    def __init__(
        self,
        runs: RunRepository,
        notifier: EventNotifier | None = None,
    ) -> None:
        self.runs = runs
        self.notifier = notifier or PollingEventNotifier()

    def append(self, event: ActivityEvent) -> ActivityEvent:
        appended = self.runs.append_event(event)
        if appended.run_id:
            self.notifier.publish(appended.run_id, appended.sequence)
        return appended

    def emit(
        self,
        run: Run,
        event_type: str,
        *,
        payload: dict[str, object] | None = None,
        **fields: Unpack[ActivityEventFields],
    ) -> ActivityEvent:
        """Callback-friendly API for future incremental coordinator emissions."""

        return self.append(
            ActivityEvent(
                thread_id=run.thread_id,
                workspace_id=run.workspace_id,
                run_id=run.id,
                owner_user_id=run.requested_by_user_id,
                event_type=event_type,
                payload=payload or {},
                **fields,
            )
        )

    def ingest_mission_events(
        self,
        run: Run,
        events: list[dict[str, object]],
        *,
        exclude_event_types: set[str] | None = None,
    ) -> list[ActivityEvent]:
        appended: list[ActivityEvent] = []
        excluded = exclude_event_types or set()
        for source in events:
            mapped = map_mission_event(source, run)
            if mapped is not None and mapped.event_type not in excluded:
                appended.append(self.append(mapped))
        return appended

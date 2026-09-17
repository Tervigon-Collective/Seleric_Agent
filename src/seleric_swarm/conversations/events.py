"""Append-only activity event sink and optional delivery notification ports."""

from __future__ import annotations

import asyncio
import time
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
    """Cross-worker notifier using Redis pub/sub plus a durable high-water mark."""

    _PUBLISH_SCRIPT = """
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    local incoming = tonumber(ARGV[1])
    if incoming > current then
        redis.call('SET', KEYS[1], incoming)
    end
    redis.call('PUBLISH', KEYS[2], ARGV[1])
    return math.max(current, incoming)
    """

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "seleric:activity-events:",
    ) -> None:
        self.client = client
        self.prefix = prefix

    def publish(self, run_id: str, sequence: int) -> None:
        self.client.eval(
            self._PUBLISH_SCRIPT,
            2,
            self._version_key(run_id),
            self._channel(run_id),
            sequence,
        )

    async def wait(self, run_id: str, after_sequence: int, timeout: float) -> None:
        if self._version(run_id) > after_sequence:
            return
        pubsub = self.client.pubsub()
        channel = self._channel(run_id)
        pubsub.subscribe(channel)
        try:
            # Close the subscribe race using the persisted high-water mark.
            if self._version(run_id) > after_sequence:
                return
            deadline = time.monotonic() + max(0.0, timeout)
            while (remaining := deadline - time.monotonic()) > 0:
                message = await asyncio.to_thread(
                    pubsub.get_message,
                    ignore_subscribe_messages=True,
                    timeout=min(remaining, 0.25),
                )
                if message is not None:
                    data = message.get("data", 0)
                    if int(data) > after_sequence:
                        return
                if self._version(run_id) > after_sequence:
                    return
                await asyncio.sleep(0)
        finally:
            pubsub.unsubscribe(channel)
            close = getattr(pubsub, "close", None)
            if callable(close):
                close()

    def _version(self, run_id: str) -> int:
        return int(self.client.get(self._version_key(run_id)) or 0)

    def _version_key(self, run_id: str) -> str:
        return f"{self.prefix}version:{run_id}"

    def _channel(self, run_id: str) -> str:
        return f"{self.prefix}channel:{run_id}"

    def close(self) -> object:
        return self.client.close()


def build_event_notifier(
    backend: str = "memory",
    *,
    redis_url: str = "",
    client: Any | None = None,
    prefix: str = "seleric:activity-events:",
) -> EventNotifier:
    """Build the configured low-latency event delivery adapter."""
    if backend == "memory":
        return InMemoryEventNotifier()
    if backend != "redis":
        raise ValueError(f"unknown event notifier backend: {backend}")
    if client is not None:
        return RedisEventNotifier(client, prefix=prefix)
    if not redis_url.strip():
        raise ValueError("event_notifier_backend=redis requires redis_url")
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError("redis package is required for Redis event notification") from exc
    return RedisEventNotifier(
        redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        ),
        prefix=prefix,
    )


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

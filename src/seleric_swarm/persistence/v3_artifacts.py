"""Postgres-backed V3 artifact store: evidence, findings, plans and turn records.

With ``PERSISTENCE_BACKEND=postgres`` the V3 store used to be the process-level
``InMemoryArtifactStore``: every mission's evidence lived only in the worker's
memory, grew without bound, vanished on a redeploy, and was invisible to the api
process (Office UI snapshots, follow-up grounding). The store also had no
``list_for_context``, so ``runner._latest_turn_record`` never found the previous
turn and follow-ups never inherited its period or offer.

This store keeps the in-memory dict as a hot cache for the running missions (the
agent loop reads its own evidence many times per step) and writes every artifact
to ``v3_artifacts`` in the background, in batches, so a 200-row breakdown does not
pay 200 synchronous inserts on the hot path. Reads that miss the cache — another
process's mission, a thread's earlier turns — go to Postgres. Older missions are
evicted from memory once their rows are written.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections import OrderedDict
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from seleric_swarm.conversations.contracts import Artifact
from seleric_swarm.state.artifacts import InMemoryArtifactStore

_log = logging.getLogger("seleric.persistence.v3_artifacts")

_INSERT = text(
    """INSERT INTO v3_artifacts (id, workspace_id, artifact_type, mission_id, thread_id, body, created_at)
    VALUES (:id, :workspace_id, :artifact_type, :mission_id, :thread_id, :body, :created_at)
    ON CONFLICT (id) DO NOTHING"""
)


def _row(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "workspace_id": artifact.workspace_id,
        "artifact_type": artifact.artifact_type,
        "mission_id": artifact.mission_id,
        "thread_id": artifact.thread_id,
        "body": json.dumps(artifact.model_dump(mode="json")),
        "created_at": artifact.created_at,
    }


def _artifact(body: Any) -> Artifact:
    return Artifact.model_validate(json.loads(body) if isinstance(body, str) else body)


class PostgresV3ArtifactStore(InMemoryArtifactStore):
    def __init__(
        self,
        engine: Engine,
        *,
        cached_missions: int = 200,
        batch_size: int = 500,
        flush_interval_s: float = 0.25,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._cached_missions = max(1, cached_missions)
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._missions: OrderedDict[str | None, None] = OrderedDict()
        self._pending: queue.Queue[Artifact | None] = queue.Queue()
        self._unwritten: set[str] = set()
        self._lock = threading.Lock()
        self._written = threading.Condition(self._lock)
        self._writer = threading.Thread(target=self._write_loop, name="v3-artifact-writer", daemon=True)
        self._writer.start()

    # -- writes ---------------------------------------------------------------------

    def put(self, artifact: Artifact) -> Artifact:
        stored = super().put(artifact)
        with self._lock:
            self._unwritten.add(stored.id)
        self._pending.put(stored)
        self._missions[stored.mission_id] = None
        self._missions.move_to_end(stored.mission_id)
        self._evict()
        return stored

    def _write_loop(self) -> None:
        while True:
            first = self._pending.get()
            if first is None:
                return
            batch = [first]
            while len(batch) < self._batch_size:
                try:
                    item = self._pending.get(timeout=self._flush_interval_s)
                except queue.Empty:
                    break
                if item is None:
                    self._write(batch)
                    return
                batch.append(item)
            self._write(batch)

    def _write(self, batch: list[Artifact]) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(_INSERT, [_row(a) for a in batch])
        except Exception:
            # Not retried: the mission keeps its cached copy, later readers do not.
            _log.error("v3_artifact_write_failed count=%d", len(batch), exc_info=True)
        finally:
            with self._written:
                self._unwritten.difference_update(a.id for a in batch)
                self._written.notify_all()

    def flush(self, timeout: float = 10.0) -> bool:
        """Block until every queued artifact is written (shutdown, tests)."""
        with self._written:
            return self._written.wait_for(lambda: not self._unwritten, timeout)

    def close(self) -> None:
        self.flush()
        self._pending.put(None)
        self._writer.join(timeout=5)

    def _evict(self) -> None:
        while len(self._missions) > self._cached_missions:
            mission_id, _ = next(iter(self._missions.items()))
            with self._lock:
                ids = [aid for aid, a in self._by_id.items() if a.mission_id == mission_id]
                if any(aid in self._unwritten for aid in ids):
                    return  # still being written: keep it cached for now
            self._missions.popitem(last=False)
            for aid in ids:
                self._by_id.pop(aid, None)

    # -- reads ----------------------------------------------------------------------

    def get(self, artifact_id: str) -> Artifact | None:
        found = super().get(artifact_id)
        if found is not None:
            return found
        with self._engine.begin() as conn:
            body = conn.execute(text("SELECT body FROM v3_artifacts WHERE id=:id"), {"id": artifact_id}).scalar()
        return _artifact(body) if body is not None else None

    def get_many(self, artifact_ids: list[str]) -> list[Artifact]:
        cached = {a.id: a for a in super().get_many(artifact_ids)}
        missing = [aid for aid in artifact_ids if aid not in cached]
        if missing:
            with self._engine.begin() as conn:
                rows = conn.execute(
                    text("SELECT body FROM v3_artifacts WHERE id = ANY(:ids)"), {"ids": missing}
                ).scalars()
                cached.update((a.id, a) for a in map(_artifact, rows))
        return [cached[aid] for aid in artifact_ids if aid in cached]

    def list_for_mission(self, mission_id: str) -> list[Artifact]:
        if mission_id in self._missions:
            return super().list_for_mission(mission_id)
        with self._engine.begin() as conn:
            rows = conn.execute(
                text("SELECT body FROM v3_artifacts WHERE mission_id=:m ORDER BY created_at"), {"m": mission_id}
            ).scalars()
            return [_artifact(b) for b in rows]

    def list_for_context(self, workspace_id: str, thread_id: str) -> list[Artifact]:
        """A thread's artifacts, newest first. An empty workspace_id matches any
        (the runner looks a thread up by id alone)."""
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    """SELECT body FROM v3_artifacts WHERE thread_id=:t
                    AND (:w = '' OR workspace_id=:w) ORDER BY created_at DESC LIMIT 200"""
                ),
                {"t": thread_id, "w": workspace_id or ""},
            ).scalars()
            stored = {a.id: a for a in map(_artifact, rows)}
        # Artifacts still queued for the writer are only in memory.
        stored.update(
            (a.id, a)
            for a in list(self._by_id.values())
            if a.thread_id == thread_id and (not workspace_id or a.workspace_id == workspace_id)
        )
        return sorted(stored.values(), key=lambda a: a.created_at, reverse=True)

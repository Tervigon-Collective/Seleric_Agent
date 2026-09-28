"""Snapshot in-memory conversation repositories onto local disk."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from seleric_swarm.conversations.contracts import (
    ActivityEvent,
    ApprovalDecisionEvent,
    ApprovalRequest,
    Artifact,
    Attachment,
    MemoryItem,
    MemoryPreference,
    Message,
    RollbackRecord,
    Run,
    RunAttempt,
    Thread,
    ThreadSummary,
)
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.conversations.phase7 import QueryEmbeddingHook
from seleric_swarm.conversations.repositories import ConversationRepositories
from seleric_swarm.persistence.file_store import _STATE_VERSION, _atomic_write, _read_json

_MUTATING = frozenset(
    {
        "add_attempt",
        "add_outbox",
        "add_rollback",
        "append_event",
        "associate_many",
        "cancel",
        "claim",
        "compare_and_set_attempt",
        "compare_and_set_status",
        "create",
        "delete",
        "finalize_attempt",
        "heartbeat",
        "mark_outbox_published",
        "put",
        "record_usage",
        "set_preference",
        "transition",
        "transition_failed_attempt",
        "update",
        "update_attempt",
    }
)


def _dump_models(items: dict[str, Any]) -> dict[str, Any]:
    return {key: value.model_dump(mode="json") for key, value in items.items()}


def _load_models(model: Any, items: dict[str, Any] | None) -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    for key, value in (items or {}).items():
        if isinstance(value, dict):
            loaded[key] = model.model_validate(value)
    return loaded


def _dump_model_lists(items: dict[str, list[Any]]) -> dict[str, list[Any]]:
    return {
        key: [item.model_dump(mode="json") for item in rows]
        for key, rows in items.items()
    }


def _load_model_lists(model: Any, items: dict[str, list[Any]] | None) -> dict[str, list[Any]]:
    loaded: dict[str, list[Any]] = {}
    for key, rows in (items or {}).items():
        if not isinstance(rows, list):
            continue
        loaded[key] = [model.model_validate(row) for row in rows if isinstance(row, dict)]
    return loaded


def snapshot_conversations(repositories: ConversationRepositories) -> dict[str, Any]:
    threads = repositories.threads
    messages = repositories.messages
    runs = repositories.runs
    artifacts = repositories.artifacts
    attachments = repositories.attachments
    memories = repositories.memories
    summaries = repositories.thread_summaries
    approvals = repositories.approvals
    return {
        "version": _STATE_VERSION,
        "threads": _dump_models(getattr(threads, "_items", {})),
        "messages": _dump_models(getattr(messages, "_items", {})),
        "runs": _dump_models(getattr(runs, "_items", {})),
        "run_attempts": _dump_models(getattr(runs, "_attempts", {})),
        "run_events": _dump_model_lists(getattr(runs, "_events", {})),
        "run_outbox": dict(getattr(runs, "_outbox", {})),
        "artifacts": _dump_models(getattr(artifacts, "_items", {})),
        "attachments": _dump_models(getattr(attachments, "_items", {})),
        "memories": _dump_models(getattr(memories, "_items", {})),
        "memory_usage": dict(getattr(memories, "_usage", {})),
        "memory_preferences": [
            preference.model_dump(mode="json")
            for preference in getattr(memories, "_preferences", {}).values()
        ],
        "thread_summaries": _dump_models(getattr(summaries, "_items", {})),
        "approvals": _dump_models(getattr(approvals, "_items", {})),
        "approval_events": _dump_model_lists(getattr(approvals, "_events", {})),
        "approval_rollbacks": _dump_models(getattr(approvals, "_rollbacks", {})),
    }


def restore_conversations(repositories: ConversationRepositories, payload: dict[str, Any]) -> None:
    if not payload:
        return
    repositories.threads._items = _load_models(Thread, payload.get("threads"))  # type: ignore[attr-defined]
    repositories.messages._items = _load_models(Message, payload.get("messages"))  # type: ignore[attr-defined]
    repositories.runs._items = _load_models(Run, payload.get("runs"))  # type: ignore[attr-defined]
    repositories.runs._attempts = _load_models(RunAttempt, payload.get("run_attempts"))  # type: ignore[attr-defined]
    repositories.runs._events = _load_model_lists(ActivityEvent, payload.get("run_events"))  # type: ignore[attr-defined]
    repositories.runs._outbox = {  # type: ignore[attr-defined]
        key: bool(value) for key, value in (payload.get("run_outbox") or {}).items()
    }
    repositories.artifacts._items = _load_models(Artifact, payload.get("artifacts"))  # type: ignore[attr-defined]
    repositories.attachments._items = _load_models(Attachment, payload.get("attachments"))  # type: ignore[attr-defined]
    repositories.memories._items = _load_models(MemoryItem, payload.get("memories"))  # type: ignore[attr-defined]
    repositories.memories._usage = {  # type: ignore[attr-defined]
        key: [str(item) for item in rows]
        for key, rows in (payload.get("memory_usage") or {}).items()
        if isinstance(rows, list)
    }
    preferences: dict[tuple[str, str], MemoryPreference] = {}
    for row in payload.get("memory_preferences") or []:
        if not isinstance(row, dict):
            continue
        preference = MemoryPreference.model_validate(row)
        preferences[(preference.workspace_id, preference.owner_user_id)] = preference
    repositories.memories._preferences = preferences  # type: ignore[attr-defined]
    repositories.thread_summaries._items = _load_models(  # type: ignore[attr-defined]
        ThreadSummary, payload.get("thread_summaries")
    )
    repositories.approvals._items = _load_models(ApprovalRequest, payload.get("approvals"))  # type: ignore[attr-defined]
    repositories.approvals._events = _load_model_lists(  # type: ignore[attr-defined]
        ApprovalDecisionEvent, payload.get("approval_events")
    )
    repositories.approvals._rollbacks = _load_models(  # type: ignore[attr-defined]
        RollbackRecord, payload.get("approval_rollbacks")
    )


class _SavingProxy:
    def __init__(self, inner: Any, save: Callable[[], None], defer: Callable[[], bool]) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_save", save)
        object.__setattr__(self, "_defer", defer)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name in _MUTATING and callable(attr):

            def wrapped(*args: Any, **kwargs: Any) -> Any:
                result = attr(*args, **kwargs)
                if not self._defer():
                    self._save()
                return result

            return wrapped
        return attr


def build_file_repositories(
    persist_path: str | Path,
    *,
    query_embedder: QueryEmbeddingHook | None = None,
) -> ConversationRepositories:
    path = Path(persist_path) / "conversations.json"
    inner = build_in_memory_repositories(query_embedder=query_embedder)
    restore_conversations(inner, _read_json(path))
    state = {"defer": 0}

    def save() -> None:
        _atomic_write(path, snapshot_conversations(inner))

    def deferring() -> bool:
        return state["defer"] > 0

    @contextmanager
    def unit_of_work():
        state["defer"] += 1
        try:
            with inner.transaction() as writes:
                yield writes
            save()
        finally:
            state["defer"] -= 1

    return ConversationRepositories(
        threads=_SavingProxy(inner.threads, save, deferring),  # type: ignore[arg-type]
        messages=_SavingProxy(inner.messages, save, deferring),  # type: ignore[arg-type]
        runs=_SavingProxy(inner.runs, save, deferring),  # type: ignore[arg-type]
        artifacts=_SavingProxy(inner.artifacts, save, deferring),  # type: ignore[arg-type]
        attachments=_SavingProxy(inner.attachments, save, deferring),  # type: ignore[arg-type]
        memories=_SavingProxy(inner.memories, save, deferring),  # type: ignore[arg-type]
        thread_summaries=_SavingProxy(inner.thread_summaries, save, deferring),  # type: ignore[arg-type]
        search=inner.search,
        approvals=_SavingProxy(inner.approvals, save, deferring),  # type: ignore[arg-type]
        unit_of_work=unit_of_work,
    )

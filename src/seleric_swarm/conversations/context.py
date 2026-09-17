"""Deterministic Phase 6 memory, summary, extraction, and context services."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from seleric_swarm.conversations.contracts import (
    Artifact,
    ContextBundle,
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Message,
    MessagePartType,
    Thread,
    ThreadSummary,
)
from seleric_swarm.conversations.repositories import ConversationRepositories

_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_CANDIDATES: tuple[tuple[re.Pattern[str], MemoryType], ...] = (
    (
        re.compile(r"\b(?:i|we)\s+(?:strongly\s+)?prefer\b", re.IGNORECASE),
        MemoryType.PREFERENCE,
    ),
    (
        re.compile(r"\b(?:i|we)\s+(?:define|mean by)\b|\bmeans\b", re.IGNORECASE),
        MemoryType.DEFINITION,
    ),
    (
        re.compile(
            r"\b(?:must|must not|cannot|never|always|required to)\b", re.IGNORECASE
        ),
        MemoryType.CONSTRAINT,
    ),
    (
        re.compile(
            r"\b(?:i|we)\s+(?:decided|have decided|choose|chose|agreed)\b",
            re.IGNORECASE,
        ),
        MemoryType.DECISION,
    ),
)


def normalize_memory_content(value: str | dict[str, Any]) -> str:
    text = value if isinstance(value, str) else " ".join(f"{k}:{value[k]}" for k in sorted(value))
    return " ".join(_WORD.findall(_SPACE.sub(" ", text).casefold()))


def _text(message: Message) -> str:
    return "\n".join(
        str(part.content).strip()
        for part in message.parts
        if part.type is MessagePartType.TEXT
        and isinstance(part.content, str)
        and part.content.strip()
    )


class ThreadSummaryService:
    """Produces source-linked summaries containing only source message text."""

    def __init__(self, repositories: ConversationRepositories) -> None:
        self.repositories = repositories

    def create(
        self,
        thread: Thread,
        messages: Iterable[Message],
        *,
        max_characters: int = 4000,
        mode: str = "extractive",
    ) -> ThreadSummary:
        source = [message for message in messages if message.thread_id == thread.id and _text(message)]
        lines: list[str] = []
        used: list[Message] = []
        remaining = max(1, max_characters)
        for message in source:
            text = _text(message)
            line = text if mode == "extractive" else f"{message.role.value}: {text}"
            if len(line) > remaining:
                break
            lines.append(line)
            used.append(message)
            remaining -= len(line) + 1
        evidence_ids = [
            str(part.content["evidence_id"])
            for message in used
            for part in message.parts
            if part.type is MessagePartType.SOURCE
            and isinstance(part.content, dict)
            and part.content.get("evidence_id")
        ]
        summary = ThreadSummary(
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            owner_user_id=thread.owner_user_id,
            through_message_id=used[-1].id if used else None,
            covered_message_ids=[message.id for message in used],
            source_evidence_ids=list(dict.fromkeys(evidence_ids)),
            mode="deterministic" if mode == "deterministic" else "extractive",
            summary="\n".join(lines),
            metadata={"faithful": True, "truncated": len(used) < len(source)},
        )
        return self.repositories.thread_summaries.create(summary)


class MemoryCandidateExtractor:
    """Conservatively extracts only explicit durable user statements."""

    def extract(
        self,
        message: Message,
        thread: Thread,
    ) -> list[MemoryItem]:
        if message.role.value != "USER":
            return []
        candidates: list[MemoryItem] = []
        for sentence in _SENTENCE.split(_text(message)):
            sentence = sentence.strip()
            matched = next(
                ((pattern, memory_type) for pattern, memory_type in _CANDIDATES if pattern.search(sentence)),
                None,
            )
            if not matched or len(sentence) < 8:
                continue
            pattern, memory_type = matched
            match = pattern.search(sentence)
            key = normalize_memory_content(sentence[(match.end() if match else 0) :])[:160]
            candidates.append(
                MemoryItem(
                    workspace_id=thread.workspace_id,
                    owner_user_id=thread.owner_user_id,
                    project_id=thread.project_id,
                    thread_id=thread.id,
                    scope=MemoryScope.USER if memory_type is MemoryType.PREFERENCE else MemoryScope.THREAD,
                    type=memory_type,
                    content=sentence,
                    normalized_content=normalize_memory_content(sentence),
                    structured_data={"subject_key": key},
                    provenance={"extractor": "phase6-explicit-v1", "verbatim": True},
                    confidence=0.9,
                    source_message_id=message.id,
                    source_message_ids=[message.id],
                    requires_confirmation=True,
                )
            )
        return candidates


def memories_contradict(left: MemoryItem, right: MemoryItem) -> bool:
    left_key = str(left.structured_data.get("subject_key") or "")
    right_key = str(right.structured_data.get("subject_key") or "")
    if not left_key or not right_key:
        return False
    left_words, right_words = set(left_key.split()), set(right_key.split())
    overlap = len(left_words & right_words) / max(1, min(len(left_words), len(right_words)))
    left_neg = bool({"not", "never", "cannot"} & set(left.normalized_content.split()))
    right_neg = bool({"not", "never", "cannot"} & set(right.normalized_content.split()))
    return overlap >= 0.6 and left_neg != right_neg


class MemoryService:
    def __init__(self, repositories: ConversationRepositories) -> None:
        self.repositories = repositories

    def ingest_candidates(self, candidates: Iterable[MemoryItem]) -> list[MemoryItem]:
        created: list[MemoryItem] = []
        for candidate in candidates:
            if self.repositories.memories.get_preference(
                candidate.workspace_id, candidate.owner_user_id
            ).opted_out:
                continue
            normalized = candidate.normalized_content or normalize_memory_content(candidate.content)
            candidate = candidate.model_copy(update={"normalized_content": normalized})
            existing = self.repositories.memories.list(
                candidate.workspace_id,
                candidate.owner_user_id,
                project_id=candidate.project_id,
                thread_id=candidate.thread_id,
                include_inactive=True,
                limit=500,
            )
            duplicate = next(
                (
                    item
                    for item in existing
                    if item.normalized_content == normalized
                    and item.type is candidate.type
                    and item.status is not MemoryStatus.DELETED
                ),
                None,
            )
            if duplicate:
                continue
            conflict = next((item for item in existing if memories_contradict(item, candidate)), None)
            if conflict:
                candidate = candidate.model_copy(
                    update={
                        "status": MemoryStatus.PENDING_CONSENT,
                        "supersedes_id": conflict.id,
                        "provenance": {**candidate.provenance, "contradicts_id": conflict.id},
                    }
                )
            created.append(self.repositories.memories.create(candidate))
        return created

    def confirm(self, memory: MemoryItem) -> MemoryItem:
        now = datetime.now(UTC)
        confirmed = memory.model_copy(
            update={
                "status": MemoryStatus.ACTIVE,
                "consented_at": now,
                "requires_confirmation": False,
                "updated_at": now,
            }
        )
        confirmed = self.repositories.memories.update(confirmed)
        if confirmed.supersedes_id:
            prior = self.repositories.memories.get(
                confirmed.supersedes_id, confirmed.workspace_id, confirmed.owner_user_id
            )
            if prior:
                self.repositories.memories.update(
                    prior.model_copy(
                        update={
                            "status": MemoryStatus.SUPERSEDED,
                            "superseded_by_id": confirmed.id,
                            "valid_to": now,
                            "updated_at": now,
                        }
                    )
                )
        return confirmed

    def expire(self, workspace_id: str, owner_user_id: str) -> int:
        now = datetime.now(UTC)
        changed = 0
        for item in self.repositories.memories.list(
            workspace_id, owner_user_id, include_inactive=True, limit=1000
        ):
            if item.status is MemoryStatus.ACTIVE and item.expires_at and item.expires_at <= now:
                self.repositories.memories.update(
                    item.model_copy(
                        update={"status": MemoryStatus.ARCHIVED, "archived_at": now, "updated_at": now}
                    )
                )
                changed += 1
        return changed


class ContextBuilder:
    def __init__(
        self,
        repositories: ConversationRepositories,
        *,
        vector_scorer: Callable[[str, list[MemoryItem]], dict[str, float]] | None = None,
        total_characters: int = 12000,
        message_characters: int = 5000,
        summary_characters: int = 2500,
        memory_characters: int = 3000,
        artifact_characters: int = 1500,
    ) -> None:
        self.repositories = repositories
        self.vector_scorer = vector_scorer
        self.total_characters = total_characters
        self.source_budgets = {
            "messages": message_characters,
            "summary": summary_characters,
            "memories": memory_characters,
            "artifacts": artifact_characters,
        }

    @staticmethod
    def _score(query: str, memory: MemoryItem, now: datetime) -> float:
        query_words = set(_WORD.findall(query.casefold()))
        memory_words = set(memory.normalized_content.split())
        lexical = len(query_words & memory_words) / max(1, len(query_words | memory_words))
        age_days = max(0.0, (now - memory.updated_at).total_seconds() / 86400)
        recency = 1 / (1 + age_days / 30)
        return lexical * 0.5 + recency * 0.15 + memory.confidence * 0.2 + memory.salience * 0.15 + (
            0.25 if memory.pinned else 0
        )

    def build(
        self,
        thread: Thread,
        *,
        query: str,
        workspace_config: dict[str, Any] | None = None,
    ) -> ContextBundle:
        preference = self.repositories.memories.get_preference(
            thread.workspace_id, thread.owner_user_id
        )
        messages = self.repositories.messages.list_for_thread(thread.id, limit=500)
        summary = self.repositories.thread_summaries.latest(
            thread.id, thread.workspace_id, thread.owner_user_id
        )
        memories = [] if preference.opted_out else self.repositories.memories.list(
            thread.workspace_id,
            thread.owner_user_id,
            project_id=thread.project_id,
            thread_id=thread.id,
            limit=500,
        )
        now = datetime.now(UTC)
        vector_scores = self.vector_scorer(query, memories) if self.vector_scorer else {}
        memories.sort(
            key=lambda item: self._score(query, item, now) + vector_scores.get(item.id, 0.0),
            reverse=True,
        )

        remaining = self.total_characters

        def take(values: Iterable[Any], budget: int, render: Callable[[Any], str]) -> list[Any]:
            nonlocal remaining
            selected: list[Any] = []
            used = 0
            for value in values:
                size = len(render(value))
                if size > budget - used or size > remaining:
                    continue
                selected.append(value)
                used += size
                remaining -= size
            return selected

        recent = take(
            reversed(messages),
            self.source_budgets["messages"],
            lambda item: _text(item),
        )
        recent.reverse()
        chosen_summary = summary
        if summary and len(summary.summary) > min(self.source_budgets["summary"], remaining):
            chosen_summary = None
        elif summary:
            remaining -= len(summary.summary)
        chosen_memories = take(
            memories,
            self.source_budgets["memories"],
            lambda item: str(item.content),
        )
        artifacts: list[Artifact] = []
        list_context = getattr(self.repositories.artifacts, "list_for_context", None)
        if callable(list_context):
            artifacts = take(
                list_context(thread.workspace_id, thread.id),
                self.source_budgets["artifacts"],
                lambda item: str(item.payload),
            )
        config = dict(workspace_config or {})
        config_size = len(str(config))
        if config_size > remaining:
            config = {}
        else:
            remaining -= config_size
        character_count = self.total_characters - remaining
        return ContextBundle(
            workspace_config=config,
            recent_messages=[message.model_dump(mode="json") for message in recent],
            latest_summary=chosen_summary,
            memories=chosen_memories,
            artifacts=artifacts,
            memory_ids=[item.id for item in chosen_memories],
            artifact_ids=[item.id for item in artifacts],
            character_count=character_count,
            token_estimate=(character_count + 3) // 4,
        )

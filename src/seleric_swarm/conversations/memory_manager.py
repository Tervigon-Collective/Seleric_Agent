"""Memory Manager — write filter, importance scoring, TTL/LRU enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from seleric_swarm.conversations.contracts import (
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Message,
    MessageRole,
)
from seleric_swarm.conversations.repositories import ConversationRepositories


@dataclass(frozen=True)
class MemoryWriteDecision:
    should_store: bool
    reason: str
    importance: float


@dataclass
class MemoryManagerConfig:
    max_memories_per_scope: dict[MemoryScope, int] = None
    default_ttl_days: dict[MemoryScope, int] = None
    importance_threshold: float = 0.3
    type_weights: dict[MemoryType, float] = None
    scope_weights: dict[MemoryScope, float] = None

    def __post_init__(self):
        if self.max_memories_per_scope is None:
            object.__setattr__(self, "max_memories_per_scope", {
                MemoryScope.USER: 200,
                MemoryScope.PROJECT: 100,
                MemoryScope.EPISODIC: 100,
                MemoryScope.THREAD: 50,
            })
        if self.default_ttl_days is None:
            object.__setattr__(self, "default_ttl_days", {
                MemoryScope.USER: 365,
                MemoryScope.PROJECT: 180,
                MemoryScope.EPISODIC: 90,
                MemoryScope.THREAD: 30,
            })
        if self.type_weights is None:
            object.__setattr__(self, "type_weights", {
                MemoryType.PREFERENCE: 1.0,
                MemoryType.CONSTRAINT: 1.0,
                MemoryType.DECISION: 0.9,
                MemoryType.DEFINITION: 0.8,
                MemoryType.FACT: 0.7,
                MemoryType.OUTCOME: 0.7,
                MemoryType.INSTRUCTION: 0.6,
                MemoryType.SUMMARY: 0.5,
            })
        if self.scope_weights is None:
            object.__setattr__(self, "scope_weights", {
                MemoryScope.USER: 1.0,
                MemoryScope.PROJECT: 0.9,
                MemoryScope.EPISODIC: 0.8,
                MemoryScope.THREAD: 0.7,
            })


class MemoryManager:
    """Centralized memory write/read policy enforcement."""

    def __init__(
        self,
        repositories: ConversationRepositories,
        config: MemoryManagerConfig | None = None,
    ) -> None:
        self.repositories = repositories
        self.config = config or MemoryManagerConfig()

    def should_store(self, message: Message, role: str) -> MemoryWriteDecision:
        """Decide whether a message should generate memory candidates."""
        if role == "USER":
            return MemoryWriteDecision(True, "user_message", 1.0)

        if role == "ASSISTANT":
            text = self._extract_text(message)
            if not text:
                return MemoryWriteDecision(False, "empty_assistant_message", 0.0)
            decision_indicators = [
                "decided", "decision", "conclusion", "outcome", "result",
                "failed", "succeeded", "error", "recommendation", "recommend",
                "must", "should", "constraint", "requirement", "limitation",
            ]
            importance = sum(1 for ind in decision_indicators if ind in text.lower()) / len(decision_indicators)
            return MemoryWriteDecision(
                importance >= self.config.importance_threshold,
                "assistant_decision" if importance >= self.config.importance_threshold else "low_signal_assistant",
                importance,
            )

        if role == "TOOL":
            text = self._extract_text(message)
            if not text:
                return MemoryWriteDecision(False, "empty_tool_result", 0.0)
            error_indicators = ["error", "failed", "exception", "timeout", "not found"]
            has_error = any(ind in text.lower() for ind in error_indicators)
            importance = 0.8 if has_error else 0.4
            return MemoryWriteDecision(
                importance >= self.config.importance_threshold,
                "tool_error" if has_error else "tool_result",
                importance,
            )

        return MemoryWriteDecision(False, f"unsupported_role_{role}", 0.0)

    def _extract_text(self, message: Message) -> str:
        return "\n".join(
            str(part.content).strip()
            for part in message.parts
            if part.type == "TEXT" and isinstance(part.content, str) and part.content.strip()
        )

    def score_importance(self, memory: MemoryItem) -> float:
        """Score memory importance for retention/eviction decisions."""
        base = self.config.type_weights.get(memory.type, 0.5) * self.config.scope_weights.get(memory.scope, 0.5)
        confidence = memory.confidence
        salience = memory.salience
        recency = self._recency_factor(memory.updated_at)
        access = min(1.0, (memory.provenance.get("access_count", 0) or 0) / 10.0)
        pinned = 1.0 if memory.pinned else 0.0
        return (base * 0.3 + confidence * 0.2 + salience * 0.15 + recency * 0.15 + access * 0.1 + pinned * 0.1)

    def _recency_factor(self, updated_at: datetime) -> float:
        age_days = (datetime.now(UTC) - updated_at).total_seconds() / 86400
        return 1.0 / (1.0 + age_days / 30.0)

    def enforce_limits(
        self,
        workspace_id: str,
        owner_user_id: str,
        *,
        scope: MemoryScope | None = None,
    ) -> int:
        """Enforce TTL expiry and LRU eviction per scope."""
        removed = 0
        now = datetime.now(UTC)

        scopes = [scope] if scope else list(MemoryScope)
        for mem_scope in scopes:
            ttl_days = self.config.default_ttl_days.get(mem_scope, 90)
            max_memories = self.config.max_memories_per_scope.get(mem_scope, 100)

            memories = self.repositories.memories.list(
                workspace_id, owner_user_id,
                project_id=None if mem_scope != MemoryScope.PROJECT else "any",
                thread_id=None if mem_scope != MemoryScope.THREAD else "any",
                include_inactive=False,
                limit=max_memories * 2,
            )
            memories = [m for m in memories if m.scope == mem_scope]

            # TTL expiry
            for memory in memories:
                if memory.expires_at and memory.expires_at <= now:
                    self.repositories.memories.update(
                        memory.model_copy(update={
                            "status": MemoryStatus.ARCHIVED,
                            "archived_at": now,
                            "updated_at": now,
                        })
                    )
                    removed += 1

            # LRU eviction if over limit
            if len(memories) > max_memories:
                memories.sort(key=lambda m: (self.score_importance(m), m.last_used_at or datetime.min))
                to_evict = memories[:len(memories) - max_memories]
                for memory in to_evict:
                    self.repositories.memories.update(
                        memory.model_copy(update={
                            "status": MemoryStatus.ARCHIVED,
                            "archived_at": now,
                            "updated_at": now,
                            "provenance": {**memory.provenance, "eviction": "LRU"},
                        })
                    )
                    removed += 1

        return removed

    def get_candidates_for_context(
        self,
        workspace_id: str,
        owner_user_id: str,
        *,
        project_id: str | None = None,
        thread_id: str | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """Retrieve top memories for context injection, scored by relevance."""
        memories = self.repositories.memories.list(
            workspace_id, owner_user_id,
            project_id=project_id,
            thread_id=thread_id,
            include_inactive=False,
            limit=200,
        )
        now = datetime.now(UTC)
        scored: list[tuple[MemoryItem, float]] = []
        for memory in memories:
            if memory.status != MemoryStatus.ACTIVE:
                continue
            if memory.scope == MemoryScope.THREAD and memory.thread_id != thread_id:
                continue
            if memory.scope in {MemoryScope.PROJECT, MemoryScope.EPISODIC} and memory.project_id != project_id:
                continue

            importance = self.score_importance(memory)
            relevance = self._query_relevance(query, memory) if query else 0.5
            score = importance * 0.6 + relevance * 0.4
            scored.append((memory, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [m for m, _ in scored[:limit]]

    def _query_relevance(self, query: str | None, memory: MemoryItem) -> float:
        if not query:
            return 0.5
        query_words = set(query.casefold().split())
        memory_words = set(memory.normalized_content.split())
        if not query_words or not memory_words:
            return 0.0
        return len(query_words & memory_words) / len(query_words | memory_words)
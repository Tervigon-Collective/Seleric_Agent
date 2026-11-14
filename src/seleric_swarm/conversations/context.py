"""Deterministic Phase 6 memory, summary, extraction, and context services."""

from __future__ import annotations

import json
import math
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
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
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
_LIVE_MEMORY = frozenset({MemoryStatus.ACTIVE, MemoryStatus.PENDING_CONSENT})
_DEFAULT_PERMISSIONS = {
    "read_messages": True,
    "read_memory": True,
    "read_artifacts": True,
    "read_workspace_config": True,
}


def normalize_memory_content(value: str | dict[str, Any]) -> str:
    text = value if isinstance(value, str) else " ".join(f"{k}:{value[k]}" for k in sorted(value))
    return " ".join(_WORD.findall(_SPACE.sub(" ", text).casefold()))


def approximate_token_count(value: str) -> int:
    """Conservative, dependency-free token approximation.

    Words longer than four characters consume multiple units and punctuation
    consumes one. This intentionally overestimates typical BPE tokenizers.
    """
    return sum(
        max(1, math.ceil(len(token) / 4))
        if token.isalnum() or token.isidentifier()
        else 1
        for token in _TOKEN.findall(value)
    )


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _text(message: Message) -> str:
    return "\n".join(
        str(part.content).strip()
        for part in message.parts
        if part.type is MessagePartType.TEXT
        and isinstance(part.content, str)
        and part.content.strip()
    )


def memory_identity(item: MemoryItem) -> tuple[Any, ...]:
    """Canonical identity for the scope that owns the fact.

    USER ignores thread/project; THREAD is per-thread; PROJECT/EPISODIC are per-project.
    """
    normalized = item.normalized_content or normalize_memory_content(item.content)
    if item.scope is MemoryScope.USER:
        return (item.scope, "", "", item.type, normalized)
    if item.scope is MemoryScope.THREAD:
        return (item.scope, "", item.thread_id or "", item.type, normalized)
    return (item.scope, item.project_id or "", "", item.type, normalized)


def validated_memory(item: MemoryItem, **updates: Any) -> MemoryItem:
    """Apply updates and re-run MemoryItem validators (model_copy alone does not)."""
    data = item.model_dump(mode="python")
    data.update(updates)
    return MemoryItem.model_validate(data)


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
    # Same identity dimensions (scope-aware) — not cross-scope.
    if memory_identity(left)[:3] != memory_identity(right)[:3]:
        return False
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
        """Ingest extracted memory candidates, deduplicating and resolving conflicts.

        Auto-confirmation policy:
        - THREAD-scoped memories are auto-confirmed to ACTIVE on creation.
          They are ephemeral (scoped to one conversation), carry no cross-user
          risk, and are useless if they never reach the agent's context window.
        - USER-scoped memories (preferences, global constraints) keep
          requires_confirmation=True because they persist across sessions and
          can affect future unrelated conversations — explicit consent is warranted.
        - Conflicting items always go to PENDING_CONSENT regardless of scope
          because a contradiction requires human resolution.
        """
        created: list[MemoryItem] = []
        now = datetime.now(UTC)
        for candidate in candidates:
            if self.repositories.memories.get_preference(
                candidate.workspace_id, candidate.owner_user_id
            ).opted_out:
                continue
            normalized = candidate.normalized_content or normalize_memory_content(candidate.content)
            candidate = validated_memory(candidate, normalized_content=normalized)
            # USER memories are cross-thread: do not narrow the duplicate/conflict
            # scan by the candidate's source thread_id.
            list_thread = None if candidate.scope is MemoryScope.USER else candidate.thread_id
            list_project = (
                candidate.project_id
                if candidate.scope in {MemoryScope.PROJECT, MemoryScope.EPISODIC}
                else None
            )
            existing = self.repositories.memories.list(
                candidate.workspace_id,
                candidate.owner_user_id,
                project_id=list_project,
                thread_id=list_thread,
                include_inactive=True,
                limit=500,
            )
            candidate_id = memory_identity(candidate)
            duplicate = next(
                (
                    item
                    for item in existing
                    if item.status in _LIVE_MEMORY and memory_identity(item) == candidate_id
                ),
                None,
            )
            if duplicate:
                merged_message_ids = list(
                    dict.fromkeys(
                        [
                            *duplicate.source_message_ids,
                            *candidate.source_message_ids,
                            *([candidate.source_message_id] if candidate.source_message_id else []),
                        ]
                    )
                )
                merged_evidence_ids = list(
                    dict.fromkeys(
                        [*duplicate.source_evidence_ids, *candidate.source_evidence_ids]
                    )
                )
                # Observation metadata only — do not bump updated_at (recency uses created_at).
                duplicate_provenance = {
                    **duplicate.provenance,
                    "duplicate_observations": int(
                        duplicate.provenance.get("duplicate_observations", 0)
                    )
                    + 1,
                    "last_observed_at": now.isoformat(),
                    "latest_duplicate_provenance": candidate.provenance,
                }
                self.repositories.memories.update(
                    validated_memory(
                        duplicate,
                        source_message_ids=merged_message_ids,
                        source_evidence_ids=merged_evidence_ids,
                        provenance=duplicate_provenance,
                    )
                )
                continue
            # Only ACTIVE memories can be contradicted; tombstones and pending drafts cannot.
            conflict = next(
                (
                    item
                    for item in existing
                    if item.status is MemoryStatus.ACTIVE and memories_contradict(item, candidate)
                ),
                None,
            )
            if conflict:
                candidate = validated_memory(
                    candidate,
                    status=MemoryStatus.PENDING_CONSENT,
                    supersedes_id=conflict.id,
                    provenance={**candidate.provenance, "contradicts_id": conflict.id},
                )
            elif candidate.scope is MemoryScope.THREAD:
                candidate = validated_memory(
                    candidate,
                    status=MemoryStatus.ACTIVE,
                    requires_confirmation=False,
                    consented_at=now,
                    updated_at=now,
                )
            created.append(self.repositories.memories.create(candidate))
        return created

    def confirm(self, memory: MemoryItem) -> MemoryItem:
        now = datetime.now(UTC)

        def _apply(writes: ConversationRepositories) -> MemoryItem:
            confirmed = writes.memories.update(
                validated_memory(
                    memory,
                    status=MemoryStatus.ACTIVE,
                    consented_at=now,
                    requires_confirmation=False,
                    updated_at=now,
                )
            )
            if confirmed.supersedes_id:
                prior = writes.memories.get(
                    confirmed.supersedes_id, confirmed.workspace_id, confirmed.owner_user_id
                )
                if prior:
                    writes.memories.update(
                        validated_memory(
                            prior,
                            status=MemoryStatus.SUPERSEDED,
                            superseded_by_id=confirmed.id,
                            valid_to=now,
                            updated_at=now,
                        )
                    )
            return confirmed

        with self.repositories.transaction() as writes:
            return _apply(writes)

    def expire(
        self,
        workspace_id: str,
        owner_user_id: str,
        *,
        now: datetime | None = None,
        on_expired: Callable[[MemoryItem], None] | None = None,
    ) -> int:
        now = now or datetime.now(UTC)
        changed = 0
        for item in self.repositories.memories.list(
            workspace_id, owner_user_id, include_inactive=True, limit=1000
        ):
            if item.status is MemoryStatus.ACTIVE and item.expires_at and item.expires_at <= now:
                expired = self.repositories.memories.update(
                    validated_memory(
                        item, status=MemoryStatus.ARCHIVED, archived_at=now, updated_at=now
                    )
                )
                if on_expired:
                    on_expired(expired)
                changed += 1
        return changed

    def revoke(
        self,
        memory_id: str,
        workspace_id: str,
        owner_user_id: str,
        *,
        reason: str | None = None,
        on_revoked: Callable[[MemoryItem], None] | None = None,
    ) -> MemoryItem | None:
        item = self.repositories.memories.get(memory_id, workspace_id, owner_user_id)
        if item is None:
            return None
        now = datetime.now(UTC)
        revoked = self.repositories.memories.update(
            validated_memory(
                item,
                status=MemoryStatus.ARCHIVED,
                valid_to=now,
                archived_at=now,
                updated_at=now,
                provenance={
                    **item.provenance,
                    "revocation": {"reason": reason, "revoked_at": now.isoformat()},
                },
            )
        )
        if on_revoked:
            on_revoked(revoked)
        return revoked


class ContextBuilder:
    def __init__(
        self,
        repositories: ConversationRepositories,
        *,
        vector_scorer: Callable[[str, list[MemoryItem]], dict[str, float]] | None = None,
        total_characters: int = 10_000,
        message_characters: int = 3_500,
        summary_characters: int = 1_200,
        memory_characters: int = 2_500,
        artifact_characters: int = 1_500,
        total_tokens: int | None = None,
    ) -> None:
        self.repositories = repositories
        self.vector_scorer = vector_scorer
        self.total_characters = total_characters
        self.total_tokens = total_tokens or max(1, total_characters // 3)
        self.source_budgets = {
            # Raw message text is now a secondary signal — TurnRecord carries
            # the grounding load for follow-ups, so we can afford a tighter
            # message budget without losing conversational accuracy.
            "messages": message_characters,
            # Thread summary is a fallback for threads with no TurnRecord.
            "summary": summary_characters,
            # Memories: 5 items × 200 chars each — sufficient for preferences
            # and constraints without crowding out the TurnRecord slot.
            "memories": memory_characters,
            "artifacts": artifact_characters,
        }

    @staticmethod
    def _score(query: str, memory: MemoryItem, now: datetime) -> float:
        query_words = set(_WORD.findall(query.casefold()))
        memory_words = set(memory.normalized_content.split())
        lexical = len(query_words & memory_words) / max(1, len(query_words | memory_words))
        # Recency from created_at: re-observing a duplicate must not refresh the score.
        age_days = max(0.0, (now - memory.created_at).total_seconds() / 86400)
        recency = 1 / (1 + age_days / 30)
        phrase = 0.2 if normalize_memory_content(query) in memory.normalized_content else 0.0
        provenance = 0.05 if (
            memory.source_message_id
            or memory.source_message_ids
            or memory.source_evidence_ids
        ) else 0.0
        return (
            lexical * 0.55
            + phrase
            + recency * 0.1
            + memory.confidence * 0.15
            + memory.salience * 0.1
            + provenance
            + (0.25 if memory.pinned else 0)
        )

    def build(
        self,
        thread: Thread,
        *,
        query: str,
        workspace_config: dict[str, Any] | None = None,
        permissions: dict[str, bool] | None = None,
    ) -> ContextBundle:
        preference = self.repositories.memories.get_preference(
            thread.workspace_id, thread.owner_user_id
        )
        # None → allow defaults. An explicit dict is deny-by-default for omitted keys
        # (so permissions={} denies everything; permissions or {...} must never be used).
        if permissions is None:
            requested_permissions = {
                **_DEFAULT_PERMISSIONS,
                "read_memory": not preference.opted_out,
            }
        else:
            requested_permissions = {
                key: bool(permissions.get(key, False)) for key in _DEFAULT_PERMISSIONS
            }
            requested_permissions["read_memory"] = (
                requested_permissions["read_memory"] and not preference.opted_out
            )

        messages: list[Message] = []
        if requested_permissions.get("read_messages", False):
            messages = self.repositories.messages.list_for_thread(thread.id, limit=500)

        summary = self.repositories.thread_summaries.latest(
            thread.id, thread.workspace_id, thread.owner_user_id
        )

        memories: list[MemoryItem] = []
        if requested_permissions.get("read_memory", False) and not preference.opted_out:
            candidates = self.repositories.memories.list(
                thread.workspace_id,
                thread.owner_user_id,
                project_id=thread.project_id,
                thread_id=thread.id,
                limit=500,
            )
            memories = [
                item
                for item in candidates
                if item.status is MemoryStatus.ACTIVE
                and (
                    item.scope is MemoryScope.USER
                    or (
                        item.scope in {MemoryScope.PROJECT, MemoryScope.EPISODIC}
                        and thread.project_id is not None
                        and item.project_id == thread.project_id
                    )
                    or (item.scope is MemoryScope.THREAD and item.thread_id == thread.id)
                )
            ]

        deduplicated: dict[tuple[Any, ...], MemoryItem] = {}
        for item in memories:
            key = memory_identity(item)
            current = deduplicated.get(key)
            if current is None or (item.pinned, item.updated_at) > (
                current.pinned,
                current.updated_at,
            ):
                deduplicated[key] = item
        memories = list(deduplicated.values())
        now = datetime.now(UTC)
        vector_scores = self.vector_scorer(query, memories) if self.vector_scorer else {}
        memory_scores = {
            item.id: self._score(query, item, now) + vector_scores.get(item.id, 0.0)
            for item in memories
        }
        memories.sort(key=lambda item: memory_scores[item.id], reverse=True)

        remaining = self.total_characters
        remaining_tokens = self.total_tokens
        omissions: list[dict[str, Any]] = []

        def take(
            values: Iterable[Any],
            budget: int,
            render: Callable[[Any], str],
            *,
            kind: str,
            id_of: Callable[[Any], str],
        ) -> list[Any]:
            nonlocal remaining, remaining_tokens
            selected: list[Any] = []
            used = 0
            for value in values:
                rendered = render(value)
                size = len(rendered)
                tokens = approximate_token_count(rendered)
                if size > budget - used or size > remaining or tokens > remaining_tokens:
                    omissions.append({"kind": kind, "id": id_of(value), "reason": "budget"})
                    continue
                selected.append(value)
                used += size
                remaining -= size
                remaining_tokens -= tokens
            return selected

        # Permissions are ACL metadata for the run record, not prompt content —
        # always keep them even when the content budget is tight.
        explicit_permissions = requested_permissions
        if not requested_permissions.get("read_messages", False):
            omissions.append({"kind": "messages", "id": "*", "reason": "permission_denied"})
        if not requested_permissions.get("read_memory", False):
            omissions.append({"kind": "memories", "id": "*", "reason": "permission_denied"})
        if preference.opted_out:
            omissions.append({"kind": "memories", "id": "*", "reason": "opted_out"})
        if not requested_permissions.get("read_artifacts", False):
            omissions.append({"kind": "artifacts", "id": "*", "reason": "permission_denied"})

        covered = set(summary.covered_message_ids) if summary else set()
        excluded_by_summary = [m.id for m in messages if m.id in covered]
        # Prefer messages not already represented in the extractive summary.
        packable = [m for m in messages if m.id not in covered] or messages
        recent = take(
            reversed(packable),
            self.source_budgets["messages"],
            _text,
            kind="message",
            id_of=lambda m: m.id,
        )
        recent.reverse()

        chosen_summary = summary
        summary_rendered = summary.summary if summary else ""
        summary_tokens = approximate_token_count(summary_rendered)
        summary_omitted_reason: str | None = None
        if summary and (
            len(summary_rendered) > min(self.source_budgets["summary"], remaining)
            or summary_tokens > remaining_tokens
        ):
            chosen_summary = None
            summary_omitted_reason = "budget"
            omissions.append({"kind": "summary", "id": summary.through_message_id or "*", "reason": "budget"})
        elif summary:
            remaining -= len(summary_rendered)
            remaining_tokens -= summary_tokens

        chosen_memories = take(
            memories,
            self.source_budgets["memories"],
            lambda item: str(item.content),
            kind="memory",
            id_of=lambda item: item.id,
        )

        artifact_candidates: list[Artifact] = []
        artifacts: list[Artifact] = []
        if requested_permissions.get("read_artifacts", False):
            list_context = getattr(self.repositories.artifacts, "list_for_context", None)
            if callable(list_context):
                artifact_candidates = list(list_context(thread.workspace_id, thread.id))
                artifacts = take(
                    artifact_candidates,
                    self.source_budgets["artifacts"],
                    lambda item: _json(item.payload),
                    kind="artifact",
                    id_of=lambda item: item.id,
                )

        config: dict[str, Any] = {}
        if requested_permissions.get("read_workspace_config", False):
            config_items = take(
                [dict(workspace_config or {})],
                remaining,
                _json,
                kind="workspace_config",
                id_of=lambda _: "workspace_config",
            )
            config = config_items[0] if config_items else {}

        provenance = {
            item.id: {
                "scope": item.scope.value,
                "project_id": item.project_id,
                "thread_id": item.thread_id,
                "source_message_ids": list(
                    dict.fromkeys(
                        [*item.source_message_ids, *([item.source_message_id] if item.source_message_id else [])]
                    )
                ),
                "source_run_id": item.source_run_id,
                "source_evidence_ids": item.source_evidence_ids,
                "provenance": item.provenance,
            }
            for item in chosen_memories
        }
        allowed_provenance: dict[str, dict[str, Any]] = {}
        for memory_id, details in provenance.items():
            rendered = _json(details)
            tokens = approximate_token_count(rendered)
            if len(rendered) <= remaining and tokens <= remaining_tokens:
                allowed_provenance[memory_id] = details
                remaining -= len(rendered)
                remaining_tokens -= tokens
            else:
                omissions.append({"kind": "memory_provenance", "id": memory_id, "reason": "budget"})
        character_count = self.total_characters - remaining
        token_estimate = self.total_tokens - remaining_tokens
        composition = {
            "builder": "phase6-v1",
            "message_ids": [message.id for message in recent],
            "excluded_by_summary": excluded_by_summary,
            "memories": [
                {"id": item.id, "score": round(memory_scores.get(item.id, 0.0), 4)}
                for item in chosen_memories
            ],
            "artifact_ids": [item.id for item in artifacts],
            "summary": (
                None
                if chosen_summary is None
                else {
                    "through_message_id": chosen_summary.through_message_id,
                    "covered_message_ids": list(chosen_summary.covered_message_ids),
                    "mode": chosen_summary.mode,
                }
            ),
            "summary_omitted_reason": summary_omitted_reason,
            "omissions": omissions[:100],
            "token_estimate": token_estimate,
            "token_budget": self.total_tokens,
            "character_count": character_count,
        }
        return ContextBundle(
            permissions=explicit_permissions,
            workspace_config=config,
            recent_messages=[message.model_dump(mode="json") for message in recent],
            latest_summary=chosen_summary,
            memories=chosen_memories,
            artifacts=artifacts,
            memory_ids=[item.id for item in chosen_memories],
            artifact_ids=[item.id for item in artifacts],
            provenance=allowed_provenance,
            character_count=character_count,
            token_estimate=token_estimate,
            token_budget=self.total_tokens,
            composition=composition,
        )

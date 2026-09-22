"""``Scratchpad`` — per-run working memory (agent tracking).

An auto-maintained ledger of facts the agent has ALREADY established this
mission (the metric values a successful ``query_metrics`` returned), rendered
back into the prompt every turn so a small model stops re-fetching / re-resolving
the same thing and burning its step budget — the exact loop behind live
``EXECUTION_LIMIT_EXCEEDED`` missions. The read path is dynamic instructions, so
there is no extra tool call, no round-trip, and no added latency: the block
rides along on the system prompt that is sent each turn anyway.

NOT evidence. A note never carries a citable number in place of an
``EvidenceArtifact`` (non-negotiable rule 6) — it restates what a successful
tool call already returned (and whose immutable artifacts already exist in the
store), so the model re-reads its own result instead of re-issuing the query.
One instance per ``SelericDeps``, never shared across missions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scratchpad:
    max_notes: int = 30
    _notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        """Append a one-line established fact; whitespace-normalized and deduped
        so a repeated resolution adds nothing to the rendered block."""
        text = " ".join(text.split())
        if not text or text in self._notes:
            return
        self._notes.append(text)
        # ponytail: drop-oldest cap so the rendered block can't balloon the
        # prompt; a bounded mission (max_tool_calls) rarely reaches it, and if
        # it does the most recent facts matter more than the first.
        if len(self._notes) > self.max_notes:
            del self._notes[0]

    def render(self) -> str:
        """The working-memory block for the system prompt, or "" when empty."""
        if not self._notes:
            return ""
        body = "\n".join(f"- {n}" for n in self._notes)
        return (
            "Facts you have ALREADY established this run (from your own "
            "successful tool calls). Do NOT re-fetch or re-resolve these — use "
            "these exact values to answer:\n" + body
        )

    def __len__(self) -> int:
        return len(self._notes)

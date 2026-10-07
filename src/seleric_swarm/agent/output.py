"""``ToolResult`` (frozen, ``CONTRACTS.md`` §2) and a draft V3 ``MissionResult``.

``ToolResult`` is frozen — every toolset function returns this shape
regardless of which of the seven toolsets produced it; the agent loop and
the ``EvidenceValidator`` reason about it directly.

``MissionResult`` here is **not** one of Sprint 0's four frozen contracts —
``CONTRACTS.md`` only froze ``SelericDeps``, ``ToolResult``, the four
artifact payload schemas, and the seven toolset signatures. The original
spec's §36 ``MissionResult`` shape lives only in the chat history that
produced ``docs/refactor/00_OVERVIEW.md``, not in any file in this repo —
this is a best-effort draft built from the non-negotiable rules (evidence
traceability, single ``as_of``, causal classification, prediction
model/version) plus the existing ``contracts/lookup.py::MissionResult`` for
field-naming consistency. Treat it as a Sprint 1 starting point, not a
frozen contract; confirm/freeze it explicitly before Profile B/C code
depends on its exact shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from seleric_swarm.conversations.contracts import ArtifactProvenance


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    artifact_ids: list[str] = Field(default_factory=list)
    summary: str
    provenance: ArtifactProvenance = Field(default_factory=ArtifactProvenance)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    retryable: bool = False

    @model_validator(mode="after")
    def _envelope_invariants(self) -> ToolResult:
        """success=False requires error_code and forbids artifact_ids (CONTRACTS.md §2)."""
        if not self.success and not self.error_code:
            raise ValueError("success=False requires error_code")
        if not self.success and self.artifact_ids:
            raise ValueError("success=False requires empty artifact_ids")
        return self


# "running" is NOT a legal terminal state — it is kept in the Literal only so a
# mid-work call still PARSES. Removing it would make pydantic reject the call at
# schema level, and output validation cannot retry under streaming (see
# _empty_means_none below), which would fail the whole mission. Instead the
# EvidenceValidator gates it and sends the model back through the revision loop,
# which re-runs the agent and works under streaming too.
MissionStatus = Literal["running", "completed", "partial", "failed"]


class MissionResult(BaseModel):
    """Draft V3 mission output — see module docstring on frozen status."""

    model_config = ConfigDict(extra="forbid")

    # mission_id / query / as_of are runner-owned: they are unconditionally
    # overwritten in runner.py (run_v3_mission model_copy) before the result is
    # stored or returned. Kept optional here so the model is not forced to
    # fabricate throwaway values (live: "m_001") — which wastes output tokens
    # and, when omitted/empty, cost a final_result validation retry.
    mission_id: str = ""
    status: MissionStatus = Field(
        description=(
            "Terminal state of the mission: 'completed' when every requested outcome is "
            "fulfilled, 'partial' when the answer is useful but incomplete, 'failed' when "
            "no requested result is usable. Calling this tool ENDS the mission, so never "
            "report 'running' — finish the tool work first."
        ),
    )
    query: str = ""
    as_of: datetime | None = None
    final_response: str
    evidence_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    not_values: list[str] = Field(
        default_factory=list,
        description=(
            "Words from the [values in the data] hints that the question uses as ordinary language, "
            "not as that value (e.g. 'other' in 'compared to other days' is not payment_method = other). "
            "Listed words are not required as filters. Never list a word the user meant as a filter "
            "for that specific dimension. However, if the hint suggests a dimension that does not match "
            "the user's intended entity type (e.g. the hint suggests ad_name but the user meant a product), "
            "you MUST list the word in not_values to reject the incorrect dimension mapping."
        ),
    )
    error_code: str | None = None
    trace: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_ids", "finding_ids", "limitations", "not_values", mode="before")
    @classmethod
    def _empty_means_none(cls, value: Any) -> Any:
        # Models regularly send "" or null for "none" (live: limitations="" and
        # limitations=None each cost a final_result retry, and under streaming —
        # which cannot retry output validation — failed the whole mission).
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [value]
        return value

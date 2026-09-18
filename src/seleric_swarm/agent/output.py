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

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.conversations.contracts import ArtifactProvenance


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    artifact_ids: list[str] = Field(default_factory=list)
    summary: str
    provenance: ArtifactProvenance
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    retryable: bool = False


MissionStatus = Literal["running", "completed", "partial", "failed"]


class MissionResult(BaseModel):
    """Draft V3 mission output — see module docstring on frozen status."""

    model_config = ConfigDict(extra="forbid")

    mission_id: str
    status: MissionStatus
    query: str
    as_of: datetime
    final_response: str
    evidence_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    error_code: str | None = None
    trace: dict[str, Any] = Field(default_factory=dict)

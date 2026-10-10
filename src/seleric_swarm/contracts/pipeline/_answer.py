"""Answer draft and document pipeline contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.contracts.pipeline._question import Ref


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    ref: Ref
    row_key: dict[str, str] = Field(default_factory=dict)
    label: str
    role: Literal["headline", "support", "change", "share", "total", "context"]


class Paragraph(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["paragraph"]
    text: str  # contains {claim_id} placeholders only for numbers


class TableBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["table"]
    result_set: Ref
    columns: tuple[str, ...]
    caption: str = ""


class ChartBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["chart"]
    refs: tuple[Ref, ...]
    chart_type: str
    title: str


class AnswerDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["completed", "partial", "failed"]
    claims: tuple[Claim, ...]
    blocks: tuple[Paragraph | TableBlock | ChartBlock, ...]
    limitations: tuple[str, ...] = ()
    next_step: str = ""


class AnswerDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    draft: AnswerDraft
    rendered_markdown: str
    evidence_ids: tuple[str, ...]

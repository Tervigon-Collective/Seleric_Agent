"""Typed turn summary from the spec, the draft and the evidence (M3.3). No regex over prose."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from seleric_swarm.answer._evidence import Evidence, resolve_claim
from seleric_swarm.contracts.pipeline import AnswerDraft, QuestionSpec, WindowSpec
from seleric_swarm.semantic import SemanticReadModel


class TurnSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    spec: QuestionSpec | None = None
    offer: str = ""
    status: str = ""
    windows: tuple[WindowSpec, ...] = ()
    metric_labels: tuple[str, ...] = ()
    claim_labels: tuple[str, ...] = ()
    entities: dict[str, tuple[str, ...]] = {}
    top_items: tuple[str, ...] = ()


def turn_summary(
    spec: QuestionSpec | None, draft: AnswerDraft, evidence: Evidence, read_model: SemanticReadModel, *, top_n: int = 5
) -> TurnSummary:
    metrics: dict[str, None] = {}
    entities: dict[str, dict[str, None]] = {}
    for e in (spec.entities if spec else ()):
        entities.setdefault(e.dimension, {}).update(dict.fromkeys(e.values))
    for claim in draft.claims:
        r = resolve_claim(evidence, claim)
        if r and r.metric_id:
            metrics[read_model.label_for(r.metric_id) or r.metric_id] = None
        for dim, val in claim.row_key.items():
            entities.setdefault(dim, {})[val] = None
    top: list[str] = []
    if spec and spec.ranking:
        for rs in evidence.result_sets():
            if rs.metric_id == spec.ranking.by_metric_id and rs.breakdowns:
                dim = rs.breakdowns[0]
                top = [r.dimensions.get(dim, "") for r in rs.rows[:top_n] if r.dimensions.get(dim)]
                break
    return TurnSummary(
        spec=spec,
        offer=draft.next_step,
        status=draft.status,
        windows=spec.windows if spec else (),
        metric_labels=tuple(metrics),
        claim_labels=tuple(dict.fromkeys(c.label for c in draft.claims if c.label)),
        entities={k: tuple(v) for k, v in entities.items()},
        top_items=tuple(top),
    )

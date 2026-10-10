"""AnswerDraft → markdown. Code writes every number; the model wrote only words and refs."""

from __future__ import annotations

from seleric_swarm.answer._evidence import Evidence, Resolved, resolve_claim
from seleric_swarm.answer._format import MISSING, format_value
from seleric_swarm.answer._text import fill
from seleric_swarm.contracts.pipeline import (
    AnswerDocument,
    AnswerDraft,
    ChartBlock,
    Paragraph,
    ResultSet,
    TableBlock,
)
from seleric_swarm.semantic import SemanticReadModel

VALUE_COLUMN = "value"


def cell(value: object) -> str:
    """One markdown table cell: pipes escaped once, line breaks folded."""
    text = " ".join(str(value).splitlines())
    return text.replace("\\|", "|").replace("|", "\\|")


def render(draft: AnswerDraft, evidence: Evidence, read_model: SemanticReadModel) -> AnswerDocument:
    claims = {c.claim_id: c for c in draft.claims}
    ids: dict[str, None] = {}

    def value_of(claim_id: str) -> str:
        claim = claims.get(claim_id)
        resolved = resolve_claim(evidence, claim) if claim else None
        if resolved is None:
            return MISSING
        ids.update(dict.fromkeys(resolved.evidence_ids))
        return format_value(resolved, read_model)

    parts: list[str] = []
    for block in draft.blocks:
        if isinstance(block, Paragraph):
            parts.append(fill(block.text, value_of))
        elif isinstance(block, TableBlock):
            item = evidence.get(block.result_set)
            if isinstance(item, ResultSet):
                parts.append(_table(item, block, read_model))
                ids.update(dict.fromkeys(r.evidence_id for r in item.rows))
        elif isinstance(block, ChartBlock):
            parts.append(f"**{block.title}**" if block.title else "")
    if draft.limitations:
        parts.append("\n".join(f"- {cell(x)}" for x in draft.limitations))
    if draft.next_step:
        parts.append(draft.next_step)
    for claim in draft.claims:  # cited claims count even when only charted
        resolved = resolve_claim(evidence, claim)
        if resolved is not None:
            ids.update(dict.fromkeys(resolved.evidence_ids))
    markdown = "\n\n".join(p for p in parts if p)
    return AnswerDocument(draft=draft, rendered_markdown=markdown, evidence_ids=tuple(ids))


def _table(rs: ResultSet, block: TableBlock, read_model: SemanticReadModel) -> str:
    dims = [c for c in block.columns if c != VALUE_COLUMN] or list(rs.breakdowns)
    value_label = read_model.label_for(rs.metric_id) or rs.metric_id
    header = [_dim_label(d, read_model) for d in dims] + [value_label]
    lines = ["| " + " | ".join(cell(h) for h in header) + " |", "|" + "---|" * len(header)]
    for row in rs.rows:
        resolved = Resolved(row.value, rs.unit, rs.metric_id, None, (row.evidence_id,))
        values = [row.dimensions.get(d, "") for d in dims] + [format_value(resolved, read_model)]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    table = "\n".join(lines)
    return f"{table}\n\n_{cell(block.caption)}_" if block.caption else table


def _dim_label(dimension: str, read_model: SemanticReadModel) -> str:
    for d in read_model.dimensions:
        if d.id == dimension:
            label = d.extra.get("label")
            return str(label) if label else dimension
    return dimension

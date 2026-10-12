"""AnswerDraft → markdown + structured Office UI parts. Code writes every number."""

from __future__ import annotations

from typing import Any

from seleric_swarm.answer._evidence import Evidence, Resolved, resolve_claim
from seleric_swarm.answer._format import MISSING, format_value
from seleric_swarm.answer._text import fill
from seleric_swarm.contracts.pipeline import (
    AnswerDocument,
    AnswerDraft,
    ChartBlock,
    CoverageBlock,
    FunnelChartBlock,
    InsightBlock,
    KpiCardBlock,
    KpiGridBlock,
    Paragraph,
    RankingChartBlock,
    RankingTableBlock,
    ResultSet,
    TableBlock,
    TimeSeriesChartBlock,
)
from seleric_swarm.contracts.pipeline._results import Derivation
from seleric_swarm.semantic import SemanticReadModel

VALUE_COLUMN = "value"
# Explanatory missing state — never spam "n/a" for an unavailable section.
_MISSING_EXPLAINED = "not available from certified evidence"


def cell(value: object) -> str:
    """One markdown table cell: pipes escaped once, line breaks folded."""
    text = " ".join(str(value).splitlines())
    return text.replace("\\|", "|").replace("|", "\\|")


def render(draft: AnswerDraft, evidence: Evidence, read_model: SemanticReadModel) -> AnswerDocument:
    claims = {c.claim_id: c for c in draft.claims}
    ids: dict[str, None] = {}
    parts: list[dict[str, Any]] = []

    def value_of(claim_id: str) -> str:
        claim = claims.get(claim_id)
        resolved = resolve_claim(evidence, claim) if claim else None
        if resolved is None:
            return _MISSING_EXPLAINED
        ids.update(dict.fromkeys(resolved.evidence_ids))
        return format_value(resolved, read_model)

    md_parts: list[str] = []
    for block in draft.blocks:
        if isinstance(block, Paragraph):
            md_parts.append(fill(block.text, value_of))
        elif isinstance(block, TableBlock):
            item = evidence.get(block.result_set)
            if isinstance(item, ResultSet):
                md_parts.append(_table(item, block, read_model))
                ids.update(dict.fromkeys(r.evidence_id for r in item.rows))
                parts.append(_table_part(item, block, read_model))
        elif isinstance(block, ChartBlock):
            chart = _legacy_chart_part(block, evidence, read_model, ids)
            if chart:
                parts.append(chart)
                md_parts.append(_chart_as_markdown(block, evidence, read_model))
            else:
                md_parts.append(f"**{block.title}**" if block.title else "")
        elif isinstance(block, KpiGridBlock):
            md_parts.append(_kpi_grid_md(block, evidence, read_model, ids))
            parts.append(_kpi_grid_part(block, evidence, read_model, ids))
        elif isinstance(block, KpiCardBlock):
            md_parts.append(_kpi_card_md(block, evidence, read_model, ids))
        elif isinstance(block, (FunnelChartBlock, TimeSeriesChartBlock, RankingChartBlock)):
            chart = _typed_chart_part(block, evidence, read_model, ids)
            if chart:
                parts.append(chart)
            md_parts.append(_typed_chart_md(block, evidence, read_model))
        elif isinstance(block, RankingTableBlock):
            item = evidence.get(block.result_set)
            if isinstance(item, ResultSet):
                fake = TableBlock(
                    kind="table",
                    result_set=block.result_set,
                    columns=block.columns or (*item.breakdowns, VALUE_COLUMN),
                    caption=block.caption,
                )
                md_parts.append(_table(item, fake, read_model))
                ids.update(dict.fromkeys(r.evidence_id for r in item.rows))
                parts.append(_table_part(item, fake, read_model))
        elif isinstance(block, InsightBlock):
            md_parts.append(_insights_md(block))
        elif isinstance(block, CoverageBlock):
            md_parts.append(_coverage_md(block))
    if draft.limitations:
        md_parts.append("### Coverage and limitations\n" + "\n".join(f"- {cell(x)}" for x in draft.limitations))
    if draft.next_step:
        md_parts.append(draft.next_step)
    for claim in draft.claims:
        resolved = resolve_claim(evidence, claim)
        if resolved is not None:
            ids.update(dict.fromkeys(resolved.evidence_ids))
    markdown = "\n\n".join(p for p in md_parts if p)
    return AnswerDocument(
        draft=draft,
        rendered_markdown=markdown,
        evidence_ids=tuple(ids),
        parts=tuple(parts),
    )


def _table(rs: ResultSet, block: TableBlock, read_model: SemanticReadModel) -> str:
    dims = [c for c in block.columns if c != VALUE_COLUMN] or list(rs.breakdowns)
    value_label = read_model.label_for(rs.metric_id) or rs.metric_id
    header = [_dim_label(d, read_model) for d in dims] + [value_label]
    lines = ["| " + " | ".join(cell(h) for h in header) + " |", "|" + "---|" * len(header)]
    for row in rs.rows:
        resolved = Resolved(row.value, rs.unit, rs.metric_id, None, (row.evidence_id,))
        shown = format_value(resolved, read_model) if row.value is not None else _MISSING_EXPLAINED
        values = [row.dimensions.get(d, "") for d in dims] + [shown]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    table = "\n".join(lines)
    return f"{table}\n\n_{cell(block.caption)}_" if block.caption else table


def _dim_label(dimension: str, read_model: SemanticReadModel) -> str:
    for d in read_model.dimensions:
        if d.id == dimension:
            label = d.extra.get("label")
            return str(label) if label else dimension
    return dimension


def _resolve_card(
    card: KpiCardBlock, evidence: Evidence, read_model: SemanticReadModel, ids: dict[str, None]
) -> dict[str, Any]:
    resolved = evidence.resolve(card.ref, card.row_key)
    value = None
    evidence_ids: list[str] = []
    if resolved is not None:
        value = resolved.value
        evidence_ids = list(resolved.evidence_ids)
        ids.update(dict.fromkeys(evidence_ids))
    comparison = None
    if card.comparison_ref is not None:
        cmp = evidence.resolve(card.comparison_ref, card.comparison_row_key)
        if cmp is not None and cmp.value is not None and value is not None:
            comparison = {"value": cmp.value, "delta": value - cmp.value}
            ids.update(dict.fromkeys(cmp.evidence_ids))
    return {
        "label": card.label,
        "value": value,
        "display": (
            format_value(Resolved(value, card.unit, None, None, tuple(evidence_ids)), read_model)
            if value is not None
            else _MISSING_EXPLAINED
        ),
        "unit": card.unit,
        "population": card.population,
        "time_basis": card.time_basis,
        "provenance": card.provenance,
        "evidence_ids": evidence_ids,
        "comparison": comparison,
        "section_id": card.section_id,
        "state": "ok" if value is not None else "missing",
    }


def _kpi_card_md(
    card: KpiCardBlock, evidence: Evidence, read_model: SemanticReadModel, ids: dict[str, None]
) -> str:
    payload = _resolve_card(card, evidence, read_model, ids)
    bits = [f"**{payload['label']}**: {payload['display']}"]
    if payload["population"]:
        bits.append(f"_{payload['population']}_")
    if payload["time_basis"]:
        bits.append(f"({payload['time_basis']})")
    return " ".join(bits)


def _kpi_grid_md(
    block: KpiGridBlock, evidence: Evidence, read_model: SemanticReadModel, ids: dict[str, None]
) -> str:
    lines = [f"### {block.title}"] if block.title else []
    for card in block.cards:
        lines.append("- " + _kpi_card_md(card, evidence, read_model, ids))
    return "\n".join(lines)


def _kpi_grid_part(
    block: KpiGridBlock, evidence: Evidence, read_model: SemanticReadModel, ids: dict[str, None]
) -> dict[str, Any]:
    return {
        "type": "KPI_GRID",
        "content": {
            "title": block.title,
            "section_id": block.section_id,
            "cards": [_resolve_card(c, evidence, read_model, ids) for c in block.cards],
        },
    }


def _table_part(rs: ResultSet, block: TableBlock, read_model: SemanticReadModel) -> dict[str, Any]:
    dims = [c for c in block.columns if c != VALUE_COLUMN] or list(rs.breakdowns)
    rows = []
    for row in rs.rows:
        rows.append(
            {
                **row.dimensions,
                "value": row.value,
                "evidence_id": row.evidence_id,
            }
        )
    return {
        "type": "TABLE",
        "content": {
            "caption": block.caption,
            "columns": [*dims, "value"],
            "metric_id": rs.metric_id,
            "unit": rs.unit,
            "rows": rows,
            "state": "ok" if rs.rows else "empty",
        },
    }


def _series_from_result_set(rs: ResultSet, read_model: SemanticReadModel) -> dict[str, Any]:
    x_key = "period_start" if any("period_start" in r.dimensions for r in rs.rows) else (
        rs.breakdowns[0] if rs.breakdowns else "label"
    )
    data = []
    for row in rs.rows:
        point = {**row.dimensions, "value": row.value, "evidence_id": row.evidence_id}
        if x_key not in point:
            point[x_key] = row.dimensions.get(x_key) or read_model.label_for(rs.metric_id) or rs.metric_id
        data.append(point)
    label = read_model.label_for(rs.metric_id) or rs.metric_id
    return {
        "chart_type": "line" if x_key == "period_start" else "bar",
        "title": label,
        "metrics": [rs.metric_id],
        "dimensions": list(rs.breakdowns) or [x_key],
        "series": [{"key": "value", "name": label, "type": "bar"}],
        "xAxis": {"key": x_key, "type": "category", "data": [str(p.get(x_key, "")) for p in data]},
        "yAxis": [{"type": "value"}],
        "data": data,
    }


def _funnel_chart_spec(block: FunnelChartBlock, evidence: Evidence, read_model: SemanticReadModel, ids: dict[str, None]) -> dict[str, Any] | None:
    data = []
    for ref in block.refs:
        item = evidence.get(ref)
        if not isinstance(item, (ResultSet, Derivation)):
            continue
        for row in item.rows:
            if row.dimensions.get("metric"):
                continue
            stage = row.dimensions.get("stage") or ""
            if row.value is None:
                continue
            ids[row.evidence_id] = None
            data.append({"stage": stage, "value": row.value, "evidence_id": row.evidence_id})
    if not data and block.stage_labels:
        # Unsupported stages: emit explanatory empty state, not zeros.
        return {
            "chart_type": "funnel",
            "title": block.title,
            "metrics": [],
            "dimensions": ["stage"],
            "series": [{"key": "value", "name": block.title, "type": "funnel"}],
            "data": [],
            "warnings": list(block.disclosures) or ["No compatible funnel stages available"],
        }
    if not data:
        return None
    return {
        "chart_type": "funnel",
        "title": block.title,
        "metrics": ["stage_count"],
        "dimensions": ["stage"],
        "series": [{"key": "value", "name": block.title or "Funnel", "type": "funnel"}],
        "xAxis": {"key": "stage", "type": "category", "data": [d["stage"] for d in data]},
        "yAxis": [{"type": "value"}],
        "data": data,
        "warnings": list(block.disclosures),
    }


def _ranking_chart_spec(block: RankingChartBlock, evidence: Evidence, read_model: SemanticReadModel, ids: dict[str, None]) -> dict[str, Any] | None:
    item = evidence.get(block.ref)
    if item is None:
        return None
    name_key = block.name_key
    data = []
    for row in item.rows:
        if row.value is None:
            continue
        # Skip null / empty identity keys when an id_key is declared.
        if block.id_key:
            identity = row.dimensions.get(block.id_key)
            if identity in (None, "", "None", "null"):
                continue
        label = (
            row.dimensions.get(name_key)
            or row.dimensions.get(block.id_key)
            or next(iter(row.dimensions.values()), "")
        )
        ids[row.evidence_id] = None
        data.append({"name": label, "value": row.value, "evidence_id": row.evidence_id, **row.dimensions})
    if not data:
        return {
            "chart_type": "bar",
            "title": block.title,
            "metrics": [],
            "dimensions": ["name"],
            "series": [{"key": "value", "name": block.criterion_label or block.title, "type": "bar"}],
            "data": [],
            "warnings": ["No eligible entities for this ranking criterion"],
        }
    return {
        "chart_type": "bar",
        "title": block.title,
        "metrics": [block.criterion_label or "value"],
        "dimensions": ["name"],
        "series": [{"key": "value", "name": block.criterion_label or block.title, "type": "bar"}],
        "xAxis": {"key": "name", "type": "category", "data": [d["name"] for d in data]},
        "yAxis": [{"type": "value"}],
        "data": data,
    }


def _typed_chart_part(block: Any, evidence: Evidence, read_model: SemanticReadModel, ids: dict[str, None]) -> dict[str, Any] | None:
    if isinstance(block, FunnelChartBlock):
        spec = _funnel_chart_spec(block, evidence, read_model, ids)
    elif isinstance(block, RankingChartBlock):
        spec = _ranking_chart_spec(block, evidence, read_model, ids)
    elif isinstance(block, TimeSeriesChartBlock):
        specs = []
        for ref in block.refs:
            item = evidence.get(ref)
            if isinstance(item, ResultSet):
                specs.append(_series_from_result_set(item, read_model))
                ids.update(dict.fromkeys(r.evidence_id for r in item.rows))
        if not specs:
            return None
        spec = specs[0]
        spec["title"] = block.title or spec.get("title")
        spec["chart_type"] = "line"
    else:
        return None
    if spec is None:
        return None
    return {"type": "CHART", "content": {"chart_type": spec["chart_type"], "artifact_id": None, "data": spec}}


def _typed_chart_md(block: Any, evidence: Evidence, read_model: SemanticReadModel) -> str:
    title = getattr(block, "title", "") or "Chart"
    lines = [f"**{title}**"]
    if isinstance(block, FunnelChartBlock) and block.disclosures:
        lines.append("Disclosures: " + "; ".join(block.disclosures))
    refs = getattr(block, "refs", None) or ((getattr(block, "ref"),) if getattr(block, "ref", None) else ())
    for ref in refs:
        item = evidence.get(ref)
        if isinstance(item, (ResultSet, Derivation)) and item.rows:
            for row in item.rows[:15]:
                row_metric = row.dimensions.get("metric")
                if row_metric == "stage_dropoff":
                    continue
                label = row.dimensions.get("stage") or row.dimensions.get("name") or str(row.dimensions)
                if row.value is None:
                    shown = _MISSING_EXPLAINED
                elif row_metric in {"stage_conversion", "stage_dropoff"} and 0.0 <= float(row.value) <= 1.0:
                    # Row-level fractions — never inherit derivation unit.
                    shown = format_value(
                        Resolved(row.value, None, None, "share", (row.evidence_id,)),
                        read_model,
                    )
                elif row_metric in {"stage_conversion", "stage_dropoff"}:
                    # Out-of-range "conversion" (cross-unit stages) — skip the row.
                    continue
                elif isinstance(item, Derivation) and item.op == "funnel_stages":
                    # Absolute stage totals: unit varies by stage metric; omit
                    # rather than print a derivation-level label.
                    shown = format_value(
                        Resolved(row.value, None, None, None, (row.evidence_id,)),
                        read_model,
                    )
                else:
                    shown = format_value(
                        Resolved(
                            row.value,
                            getattr(item, "unit", None),
                            getattr(item, "metric_id", None),
                            None,
                            (row.evidence_id,),
                        ),
                        read_model,
                    )
                lines.append(f"- {label}: {shown}")
    return "\n".join(lines)


def _legacy_chart_part(
    block: ChartBlock, evidence: Evidence, read_model: SemanticReadModel, ids: dict[str, None]
) -> dict[str, Any] | None:
    for ref in block.refs:
        item = evidence.get(ref)
        if isinstance(item, ResultSet) and item.rows:
            spec = _series_from_result_set(item, read_model)
            if block.chart_type:
                spec["chart_type"] = block.chart_type
            if block.title:
                spec["title"] = block.title
            ids.update(dict.fromkeys(r.evidence_id for r in item.rows))
            return {"type": "CHART", "content": {"chart_type": spec["chart_type"], "artifact_id": None, "data": spec}}
    return None


def _chart_as_markdown(block: ChartBlock, evidence: Evidence, read_model: SemanticReadModel) -> str:
    lines = [f"**{block.title}**"] if block.title else []
    for ref in block.refs:
        item = evidence.get(ref)
        if isinstance(item, ResultSet):
            for row in item.rows[:20]:
                label = ", ".join(f"{k}={v}" for k, v in row.dimensions.items()) or (
                    read_model.label_for(item.metric_id) or item.metric_id
                )
                shown = (
                    format_value(Resolved(row.value, item.unit, item.metric_id, None, (row.evidence_id,)), read_model)
                    if row.value is not None
                    else _MISSING_EXPLAINED
                )
                lines.append(f"- {label}: {shown}")
    return "\n".join(lines) if len(lines) > 1 else (lines[0] if lines else "")


def _insights_md(block: InsightBlock) -> str:
    lines = [f"### {block.title}"] if block.title else []
    for insight in block.insights:
        lines.append(f"- **{insight.movement}** ({insight.explanation_kind})")
        if insight.limitation:
            lines.append(f"  - Limitation: {insight.limitation}")
        if insight.next_check:
            lines.append(f"  - Next check: {insight.next_check}")
    return "\n".join(lines)


def _coverage_md(block: CoverageBlock) -> str:
    if block.status == "complete" and not block.notes:
        return ""
    lines = [f"**Coverage ({block.status})**"]
    for note in block.notes:
        # Never expose internal step ids like q1/d1 in business-facing text.
        if _looks_internal_id(note):
            continue
        lines.append(f"- {note}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _looks_internal_id(text: str) -> bool:
    tokens = text.replace(",", " ").replace(":", " ").split()
    return any(
        (t.startswith("q") or t.startswith("d") or t.startswith("e") or t.startswith("s"))
        and len(t) <= 4
        and t[1:].isdigit()
        for t in tokens
    )

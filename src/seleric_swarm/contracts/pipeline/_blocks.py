"""Typed server-side presentation blocks (AG-08). Values resolve from evidence refs only."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.contracts.pipeline._question import Ref
from seleric_swarm.contracts.pipeline._report import CoverageStatus, ExplanationKind, Insight


class KpiCardBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["kpi_card"] = "kpi_card"
    ref: Ref
    row_key: dict[str, str] = Field(default_factory=dict)
    label: str
    unit: str | None = None
    population: str = ""
    time_basis: str = ""
    provenance: str = ""
    comparison_ref: Ref | None = None
    comparison_row_key: dict[str, str] = Field(default_factory=dict)
    section_id: str = ""


class KpiGridBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["kpi_grid"] = "kpi_grid"
    cards: tuple[KpiCardBlock, ...] = ()
    title: str = ""
    section_id: str = ""


class TimeSeriesChartBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["timeseries_chart"] = "timeseries_chart"
    refs: tuple[Ref, ...]
    title: str = ""
    x_key: str = "period_start"
    section_id: str = ""


class FunnelChartBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["funnel_chart"] = "funnel_chart"
    refs: tuple[Ref, ...]
    stage_labels: tuple[str, ...] = ()
    title: str = ""
    section_id: str = ""
    disclosures: tuple[str, ...] = ()


class RankingChartBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ranking_chart"] = "ranking_chart"
    ref: Ref
    title: str = ""
    criterion_label: str = ""
    name_key: str = ""
    id_key: str = ""
    section_id: str = ""


class RankingTableBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ranking_table"] = "ranking_table"
    result_set: Ref
    columns: tuple[str, ...] = ()
    caption: str = ""
    criterion_label: str = ""
    section_id: str = ""


class InsightBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["insight"] = "insight"
    insights: tuple[Insight, ...] = ()
    title: str = "Top learnings"
    section_id: str = ""


class CoverageBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["coverage"] = "coverage"
    status: CoverageStatus
    notes: tuple[str, ...] = ()
    section_id: str = ""


ReportBlock = (
    KpiCardBlock
    | KpiGridBlock
    | TimeSeriesChartBlock
    | FunnelChartBlock
    | RankingChartBlock
    | RankingTableBlock
    | InsightBlock
    | CoverageBlock
)

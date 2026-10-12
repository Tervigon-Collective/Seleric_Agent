"""Frozen pipeline contracts (Part B). Public surface only — see ``__all__``."""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypeVar

from pydantic import BaseModel

from seleric_swarm.contracts.pipeline._answer import (
    AnswerBlock,
    AnswerDocument,
    AnswerDraft,
    ChartBlock,
    Claim,
    Paragraph,
    TableBlock,
)
from seleric_swarm.contracts.pipeline._blocks import (
    CoverageBlock,
    FunnelChartBlock,
    InsightBlock,
    KpiCardBlock,
    KpiGridBlock,
    RankingChartBlock,
    RankingTableBlock,
    TimeSeriesChartBlock,
)
from seleric_swarm.contracts.pipeline._draft import (
    ForecastHorizonSlot,
    ForecastSlots,
    MeasurePhrase,
    ObjectiveDraft,
    QuestionDraft,
    WindowEnding,
    WindowExpr,
    WindowSlot,
)
from seleric_swarm.contracts.pipeline._errors import StageError
from seleric_swarm.contracts.pipeline._plan import (
    AnalysisPlan,
    DeriveStep,
    EngineStep,
    JsonValue,
    QueryStep,
)
from seleric_swarm.contracts.pipeline._question import (
    Clarification,
    EntitySpec,
    FilterSpec,
    MeasureSpec,
    QuestionSpec,
    RankingSpec,
    Ref,
    WindowSpec,
)
from seleric_swarm.contracts.pipeline._report import (
    AcquisitionAttempt,
    FunnelDefinition,
    FunnelStage,
    Insight,
    ReportCoverage,
    ReportPlan,
    ReportSpec,
    SectionCoverage,
    SectionPlan,
    SectionSpec,
)
from seleric_swarm.contracts.pipeline._results import Derivation, ResultRow, ResultSet

__all__ = [
    "AcquisitionAttempt",
    "AnalysisPlan",
    "AnswerBlock",
    "AnswerDocument",
    "AnswerDraft",
    "ChartBlock",
    "Claim",
    "Clarification",
    "CoverageBlock",
    "Derivation",
    "DeriveStep",
    "EngineStep",
    "EntitySpec",
    "FilterSpec",
    "ForecastHorizonSlot",
    "ForecastSlots",
    "FunnelChartBlock",
    "FunnelDefinition",
    "FunnelStage",
    "Insight",
    "InsightBlock",
    "JsonValue",
    "KpiCardBlock",
    "KpiGridBlock",
    "MeasurePhrase",
    "MeasureSpec",
    "ObjectiveDraft",
    "Paragraph",
    "QueryStep",
    "QuestionDraft",
    "QuestionSpec",
    "RankingChartBlock",
    "RankingSpec",
    "RankingTableBlock",
    "Ref",
    "ReportCoverage",
    "ReportPlan",
    "ReportSpec",
    "ResultRow",
    "ResultSet",
    "SectionCoverage",
    "SectionPlan",
    "SectionSpec",
    "StageError",
    "TableBlock",
    "TimeSeriesChartBlock",
    "WindowEnding",
    "WindowExpr",
    "WindowSlot",
    "WindowSpec",
    "dump",
    "fingerprint",
    "load",
]

_T = TypeVar("_T", bound=BaseModel)


def dump(model: BaseModel) -> str:
    """Serialize a contract model to canonical JSON (sorted keys, no whitespace)."""
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def load(data: str | bytes | dict[str, Any], model_type: type[_T]) -> _T:
    """Deserialize JSON (or a plain dict) into ``model_type``."""
    if isinstance(data, dict):
        return model_type.model_validate(data)
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return model_type.model_validate_json(data)


def fingerprint(model: BaseModel) -> str:
    """sha256 of the canonical JSON dump (for stage spans)."""
    return hashlib.sha256(dump(model).encode("utf-8")).hexdigest()

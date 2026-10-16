"""The result of one driver's causal estimate, as the engine reads it (moved from ``causal/dowhy_service.py``)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from seleric_swarm.diagnosis.domain.canonical import fingerprint


@dataclass
class TimeSeriesEffect:
    treatment: str
    outcome: str
    status: Literal["estimated", "insufficient"]
    reason: str = ""
    effect: float | None = None
    std_error: float | None = None
    p_value: float | None = None
    confidence_interval: list[float] = field(default_factory=list)
    n_rows: int = 0
    adjustment_set: list[str] = field(default_factory=list)
    graph_edges: list[tuple[str, str]] = field(default_factory=list)
    identified_estimand: str = ""
    estimator: str = "backdoor.linear_regression"
    uncertainty_method: str = "max(HC3, small-sample HAC) standard error, t interval"
    refutations: list[dict[str, Any]] = field(default_factory=list)
    temporal: dict[str, Any] = field(default_factory=dict)
    naive_association: dict[str, Any] = field(default_factory=dict)
    # Steps that failed inside the estimate and were replaced by a weaker fallback or left out: the worker cannot
    # reach the trace, so it hands them back and the stage reports each one as an ``operator_failed`` event.
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def refutations_passed(self) -> bool:
        return bool(self.refutations) and all(r.get("passed") for r in self.refutations)

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items()}
        out["graph_edges"] = [list(e) for e in self.graph_edges]
        return out


_NON_FINITE = "$float"


def _encode(value: Any) -> Any:
    """A JSON form that keeps what JSON cannot: a non-finite float becomes ``{"$float": "nan"}``, a date its ISO string."""
    if hasattr(value, "item") and not isinstance(value, (list, tuple, dict, str)):  # numpy scalar
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return {_NON_FINITE: "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k) if not isinstance(k, date) else k.isoformat(): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {_NON_FINITE}:
            return float(value[_NON_FINITE])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def job_key(job: Mapping[str, Any]) -> str:
    """What identifies an estimation request: a digest of everything it was asked with."""
    return fingerprint(_encode(dict(job)), length=16)


def effect_to_json(effect: TimeSeriesEffect) -> dict[str, Any]:
    body: dict[str, Any] = _encode(effect.as_dict())
    return body


def effect_from_json(raw: Mapping[str, Any]) -> TimeSeriesEffect:
    body = _decode(dict(raw))
    body["graph_edges"] = [tuple(e) for e in body.get("graph_edges") or []]
    return TimeSeriesEffect(**body)

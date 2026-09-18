"""Prediction → actual feedback loop (spec §20, Profile C Sprint 3).

A forecast that is never scored against what actually happened is a number with
no accountability. This pairs a stored ``PredictionArtifact`` with the
``EvidenceArtifact`` that later measured the same metric over the same period,
and reports the error.

Pure functions over artifacts — no fetching (rule 5), no store access. The
caller supplies both sides; deciding *when* enough time has passed to evaluate
is an orchestration question, not this module's.

Honest limitation
-----------------
This measures accuracy; it does not yet feed back into model selection or
promote/demote a registry entry. ``config/model_registry.yaml``'s
``last_validated_at`` is still set by hand. Closing that loop needs a scheduled
job and a registry writer, neither of which exists — so this is the measurement
half only, and saying so beats implying the loop is closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from seleric_swarm.agent.artifacts import EvidenceArtifact, PredictionArtifact


@dataclass(frozen=True)
class PredictionError:
    model_id: str
    model_version: str
    metric_id: str
    period_start: datetime
    predicted: float
    actual: float
    absolute_error: float
    percentage_error: float | None
    within_interval: bool | None

    @property
    def interval_missed(self) -> bool:
        """True only when an interval existed and the actual fell outside it.

        A missing interval is not a miss — it is an unanswerable question, and
        collapsing the two would flatter a model that never stated uncertainty.
        """
        return self.within_interval is False


def evaluate_prediction(
    prediction: PredictionArtifact, actual: EvidenceArtifact
) -> PredictionError | None:
    """Score one prediction against one measured outcome.

    ``None`` when the pair cannot be scored — a null actual, or a prediction
    for a different metric. Returning a zero error would be a lie.
    """
    if actual.value is None:
        return None
    if prediction.prediction_type != "forecast":
        return None

    predicted = float(prediction.value)
    measured = float(actual.value)
    absolute = abs(predicted - measured)
    percentage = (absolute / abs(measured) * 100) if measured else None

    within: bool | None = None
    if prediction.confidence_interval is not None:
        low, high = sorted(prediction.confidence_interval)
        within = low <= measured <= high

    return PredictionError(
        model_id=prediction.model_id,
        model_version=prediction.model_version,
        metric_id=actual.metric_id,
        period_start=actual.period_start,
        predicted=predicted,
        actual=measured,
        absolute_error=round(absolute, 6),
        percentage_error=round(percentage, 4) if percentage is not None else None,
        within_interval=within,
    )


def pair_predictions_with_actuals(
    predictions: list[tuple[PredictionArtifact, str, datetime]],
    actuals: list[EvidenceArtifact],
) -> list[PredictionError]:
    """Match each prediction to the evidence measuring its target period.

    Each entry is ``(prediction, metric_id, target_period)``. Both the metric
    and the period come from the caller because ``PredictionArtifact`` records
    the value and the model but neither the target metric nor the horizon date —
    those live in the calling mission's context. Deriving the metric by
    string-matching the ``model_id`` would reintroduce exactly the
    name-guessing heuristic this migration retires (``docs/BUG_SHEET.md`` #8).

    An unmatched prediction is skipped silently: the actual simply hasn't been
    measured yet, which is the normal state for a forecast.
    """
    by_key = {(item.metric_id, item.period_start): item for item in actuals}

    out: list[PredictionError] = []
    for prediction, metric_id, target_period in predictions:
        evidence = by_key.get((metric_id, target_period))
        if evidence is None:
            continue
        scored = evaluate_prediction(prediction, evidence)
        if scored is not None:
            out.append(scored)
    return out


def summarize(errors: list[PredictionError]) -> dict[str, float]:
    """MAE / MAPE / interval coverage across a set of scored predictions.

    Coverage counts only predictions that stated an interval, for the reason in
    ``PredictionError.interval_missed``.
    """
    if not errors:
        return {}

    absolute = [e.absolute_error for e in errors]
    percentages = [e.percentage_error for e in errors if e.percentage_error is not None]
    with_interval = [e for e in errors if e.within_interval is not None]

    summary = {
        "count": float(len(errors)),
        "mae": round(sum(absolute) / len(absolute), 6),
    }
    if percentages:
        summary["mape"] = round(sum(percentages) / len(percentages), 4)
    if with_interval:
        covered = sum(1 for e in with_interval if e.within_interval)
        summary["interval_coverage"] = round(covered / len(with_interval), 4)
        summary["interval_sample"] = float(len(with_interval))
    return summary

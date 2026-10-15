from __future__ import annotations

from typing import TYPE_CHECKING, Any

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.diagnosis.detect.detector import RobustScore, rescore, robust_score
from seleric_swarm.domain.models import StateRequest
from seleric_swarm.swarm.providers.base import AnomalyFinding, MetricReading

if TYPE_CHECKING:
    from seleric_swarm.services.business_state.facade import BusinessStateService

STRATEGY_ID = "robust_zscore"
STRATEGY_VERSION = "v1"

# One implementation of the median + MAD band lives in the shared detector (plan P1.4); the names below are kept.
RobustZScoreResult = RobustScore


def robust_zscore(history: list[float], observed: float, *, z_threshold: float = 3.0) -> RobustZScoreResult:
    """Median + MAD (median absolute deviation) anomaly score.

    Robust to outliers in ``history`` unlike a mean/stdev z-score -- one bad
    day in the window doesn't blow out the "expected" band. ``history``
    should exclude ``observed`` itself.
    """
    if not history:
        raise ValueError("robust_zscore needs at least one history point")
    return robust_score(history, observed, z_threshold=z_threshold)


def _rescore_against_expected(
    observed: float, *, expected: float, expected_range: list[float], z_threshold: float
) -> RobustZScoreResult:
    """Score an arbitrary ``observed`` against an already-computed median +
    MAD band, instead of the band's own last-history-point observation."""
    return rescore(observed, expected=expected, expected_range=expected_range, z_threshold=z_threshold)


class RobustZScoreDetector:
    """``AnomalyDetector`` Protocol implementation backed by
    ``BusinessStateService`` history (median + MAD), instead of the
    Template's single value/baseline relative-effect check.

    Protocol-conformant today so it can be dropped into ``ProviderBundle``
    directly; the config-driven *selection* of it over
    ``TemplateAnomalyDetector`` inside ``build_mcp_bundle()`` is Sprint 2.5
    (05_SPRINT_PLAN.md) -- not implemented here.
    """

    def __init__(self, business_state: BusinessStateService) -> None:
        self._business_state = business_state

    async def detect(self, readings: list[MetricReading], *, context: dict[str, Any]) -> list[AnomalyFinding]:
        time_range_payload = context.get("time_range") or {"kind": "none"}
        findings: list[AnomalyFinding] = []
        for reading in readings:
            if reading.value is None:
                continue
            definition = self._business_state.runtime.metrics.get(reading.metric_id)
            if definition is None:
                continue
            request = StateRequest(
                metric_id=reading.metric_id,
                time_range=TimeRangeV1.model_validate(time_range_payload),
                dimensions=reading.dimensions,
                agent_id=f"{definition.domain}_agent",
                need=["anomaly"],
            )
            state = await self._business_state.get_metric_state(request)
            if state.anomaly is None:
                continue
            anomaly = state.anomaly
            # ``anomaly["observed"]`` is BusinessStateService's own last
            # history point (e.g. a daily-series lookup anchored on the
            # request window's end date) — NOT necessarily what this
            # reading's evidence pipeline actually fetched (a monthly total,
            # a comparison period, a breakdown slice, ...). Rescore
            # reading.value against the same expected band instead of
            # silently substituting a different number for it.
            rescored = _rescore_against_expected(
                reading.value,
                expected=anomaly["expected"],
                expected_range=anomaly["expected_range"],
                z_threshold=anomaly["detector"]["z_threshold"],
            )
            adverse = rescored.direction == reading.direction_bad
            findings.append(
                AnomalyFinding(
                    metric_id=reading.metric_id,
                    observed=rescored.observed,
                    expected_range=rescored.expected_range,
                    deviation_pct=rescored.deviation_pct,
                    score=rescored.score,
                    direction=rescored.direction,
                    direction_bad=reading.direction_bad,
                    adverse=adverse,
                    magnitude_score=rescored.score,
                    adversity_score=rescored.score if adverse else 0.0,
                    detector=anomaly["detector"],
                    dimensions=reading.dimensions,
                    data_origin="BUSINESS_STATE",
                    synthetic=False,
                )
            )
        return findings

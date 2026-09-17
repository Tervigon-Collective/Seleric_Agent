from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import FeatureValue, MetricState, QualityFlag, SeriesPoint, StateRequest
from seleric_swarm.services.business_state.detectors import robust_zscore
from seleric_swarm.services.business_state.features import classify_freshness, compute_features
from seleric_swarm.services.business_state.profiles import ProfileLoader
from seleric_swarm.services.business_state.series import fetch_series
from seleric_swarm.services.time_range import resolve_time_range

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _parse_days(window: str | None, default: int) -> int:
    if not window or not window.endswith("d"):
        return default
    try:
        return int(window[:-1])
    except ValueError:
        return default


class BusinessStateService:
    """Deterministic metric state: MCP series -> features -> MetricState.

    Sprint 1: ``actual`` + the 5 daily_series features, for one metric per
    call. Sprint 2 adds ``anomaly`` (robust z-score / MAD, see detectors.py).
    Forecast and caching are still deferred to Sprint 2.5/6 -- unsupported
    ``need`` entries are accepted and silently ignored.
    """

    def __init__(self, runtime: SwarmRuntime, profiles: ProfileLoader | None = None) -> None:
        self._runtime = runtime
        self._profiles = profiles or ProfileLoader()

    @property
    def runtime(self) -> SwarmRuntime:
        return self._runtime

    async def get_metric_state(self, request: StateRequest) -> MetricState:
        definition = self._runtime.metrics.get(request.metric_id)
        if definition is None:
            return MetricState(
                metric_id=request.metric_id,
                catalogue_metric_id=request.catalogue_metric_id or request.metric_id,
                as_of=request.as_of or _today(),
                status="UNAVAILABLE",
                error_code="INSUFFICIENT_EVIDENCE",
                quality_flags=["CATALOGUE_MISS"],
            )

        profile = self._profiles.get(request.profile_id)
        brand_id = str(request.dimensions.get("brand_id") or "20")
        series_cfg = profile.get("series") or {}
        anomaly_cfg = profile.get("anomaly") or {}

        # The caller's time_range only needs to cover what features ask for
        # (e.g. a 7d rolling window). Anomaly needs its own, usually wider,
        # history window (28d default) -- widen the *fetch* window rather
        # than making every caller know the anomaly profile's window.
        fetch_time_range = request.time_range
        if "anomaly" in request.need:
            resolved = resolve_time_range(request.time_range, definition.timezone, request.as_of)
            if resolved.end:
                end_date = date.fromisoformat(resolved.end)
                window_days = _parse_days(anomaly_cfg.get("window"), 28)
                start_date = end_date - timedelta(days=window_days)
                fetch_time_range = TimeRangeV1(kind="absolute", start=start_date.isoformat(), end=resolved.end)

        series, provenance = await fetch_series(
            self._runtime,
            definition=definition,
            agent_id=request.agent_id,
            time_range=fetch_time_range,
            brand_id=brand_id,
            grain=series_cfg.get("grain", "day"),
            max_lookback_days=series_cfg.get("max_lookback_days", 90),
        )

        if provenance.get("error") or not series:
            flag: QualityFlag = "MCP_ERROR" if provenance.get("error") else "MISSING_DATA"
            return MetricState(
                metric_id=request.metric_id,
                catalogue_metric_id=definition.catalogue_metric or request.metric_id,
                as_of=request.as_of or _today(),
                dimensions={"brand_id": brand_id},
                direction_bad=definition.direction_bad,
                status="UNAVAILABLE",
                error_code="INSUFFICIENT_EVIDENCE",
                quality_flags=[flag],
                provenance=provenance,
            )

        freshness, age_hours = classify_freshness(provenance, profile)
        features: dict[str, FeatureValue] = {}
        quality_flags: list[QualityFlag] = []
        if "features" in request.need:
            features, quality_flags = compute_features(series, profile.get("features") or [])
            wants_freshness_age = any(
                spec.get("strategy") == "freshness_age" for spec in profile.get("features") or []
            )
            if wants_freshness_age:
                features["freshness_age"] = FeatureValue(value=age_hours, strategy_version="v1")

        anomaly: dict[str, Any] | None = None
        if "anomaly" in request.need:
            anomaly, anomaly_flag = self._evaluate_anomaly(series, anomaly_cfg)
            if anomaly_flag and anomaly_flag not in quality_flags:
                quality_flags.append(anomaly_flag)

        return MetricState(
            metric_id=request.metric_id,
            catalogue_metric_id=definition.catalogue_metric or request.metric_id,
            as_of=series[-1].ts,
            window={
                "start": fetch_time_range.start,
                "end": fetch_time_range.end,
                "grain": series_cfg.get("grain", "day"),
                "timezone": definition.timezone,
            },
            dimensions={"brand_id": brand_id},
            actual=series[-1].value,
            series=series,
            features=features,
            anomaly=anomaly,
            freshness=freshness,
            direction_bad=definition.direction_bad,
            quality_flags=quality_flags,
            provenance={
                **provenance,
                "feature_profile_id": request.profile_id,
                "detector_profile_id": request.profile_id if anomaly else None,
                "metric_version": definition.version,
                "computed_at": datetime.now(UTC).isoformat(),
            },
            # SPARSE_HISTORY/MISSING_DATA on a *requested* capability (03 SS5)
            # withholds that capability's number (anomaly=None above) rather
            # than blanking the whole MetricState -- actual/features that did
            # resolve stay usable. UNAVAILABLE is reserved for "no series at
            # all" (the branch above); a partially-served request is PARTIAL.
            status="PARTIAL" if quality_flags else "OK",
        )

    async def evaluate_anomaly(self, request: StateRequest) -> dict[str, Any] | None:
        """Thin convenience wrapper -- same computation as
        ``get_metric_state(need=[..., "anomaly"])``, returning just the
        anomaly subset (or ``None`` if unavailable)."""
        need = list(dict.fromkeys([*request.need, "anomaly"]))
        state = await self.get_metric_state(request.model_copy(update={"need": need}))
        return state.anomaly

    def _evaluate_anomaly(
        self, series: list[SeriesPoint], anomaly_cfg: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, QualityFlag | None]:
        values = [p.value for p in series if p.value is not None]
        min_points = anomaly_cfg.get("min_points", 14)
        if len(values) < min_points + 1:
            return None, "SPARSE_HISTORY"
        window_days = _parse_days(anomaly_cfg.get("window"), 28)
        windowed = values[-window_days:] if window_days else values
        history, observed = windowed[:-1], windowed[-1]
        z_threshold = anomaly_cfg.get("z_threshold", 3.0)
        result = robust_zscore(history, observed, z_threshold=z_threshold)
        return {
            "observed": result.observed,
            "expected": result.expected,
            "expected_range": result.expected_range,
            "deviation_pct": result.deviation_pct,
            "score": result.score,
            "direction": result.direction,
            "is_anomaly": result.is_anomaly,
            "detector": {
                "strategy": "robust_zscore",
                "version": "v1",
                "z_threshold": z_threshold,
                "window": anomaly_cfg.get("window", "28d"),
                "min_points": min_points,
            },
        }, None

"""Backfill the business-state ready store with REAL data for the last year.

The hourly refresher only produces snapshots going forward, so the store would
have no history. This backfills one snapshot per day per domain (plus the
cross-domain ``business`` headline snapshot) for the last N days, computed from
each metric's real daily series fetched from Cube via MCP.

    uv run python -m scripts.backfill_business_state [--days 365] [--brand-id 20]

Each metric's full series is fetched ONCE (not per-day), then features
(current_value / period_delta_pct / rolling_mean_7d / rolling_std_7d) and the
robust z-score anomaly are computed per day by reusing the same
``compute_features`` / ``robust_zscore`` the live path uses. Snapshots are
written via the same ``SnapshotStore`` the fast path reads.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta

from dotenv import load_dotenv

from seleric_swarm.config.settings import Settings
from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.domain.models import SeriesPoint
from seleric_swarm.paths import repo_root
from seleric_swarm.services.business_state.detectors import robust_zscore
from seleric_swarm.services.business_state.features import compute_features
from seleric_swarm.services.business_state.profiles import ProfileLoader
from seleric_swarm.services.business_state.series import fetch_series
from seleric_swarm.services.domain_health.models import DomainStateSnapshot, ResolvedMetric
from seleric_swarm.services.domain_health.resolver import DomainHealthProfiles
from seleric_swarm.services.domain_health.scheduler import ALL_DOMAINS, build_headline_snapshot
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore
from seleric_swarm.services.mcp_query import DEFAULT_BRAND_ID


def _parse_days(window: str | None, default: int) -> int:
    if not window or not window.endswith("d"):
        return default
    try:
        return int(window[:-1])
    except ValueError:
        return default


async def _fetch_all_series(
    runtime, profiles: DomainHealthProfiles, *, start: str, end: str, brand_id: str
) -> dict[str, list[SeriesPoint]]:
    """One fetch per (domain, metric); the domain's agent_id drives module scoping."""
    tasks: dict[str, asyncio.Task] = {}
    for domain in ALL_DOMAINS:
        block = profiles.get(domain)
        agent_id = block.get("agent_id", f"{domain}_agent")
        for entry in block.get("metrics", []):
            mid = entry["metric_id"]
            if mid in tasks:
                continue
            definition = runtime.metrics.get(mid)
            if definition is None:
                print(f"  ! {mid}: not in registry, skipping")
                continue
            tr = TimeRangeV1(kind="absolute", start=start, end=end)
            lookback = (date.fromisoformat(end) - date.fromisoformat(start)).days + 5
            tasks[mid] = asyncio.create_task(
                fetch_series(
                    runtime, definition=definition, agent_id=agent_id, time_range=tr,
                    brand_id=brand_id, grain="day", max_lookback_days=lookback,
                )
            )
    series: dict[str, list[SeriesPoint]] = {}
    for mid, task in tasks.items():
        try:
            points, prov = await task
        except Exception as exc:  # noqa: BLE001 - one bad metric must not sink the backfill
            print(f"  ! {mid}: fetch failed ({type(exc).__name__}: {str(exc)[:80]})")
            continue
        if prov.get("error"):
            print(f"  ! {mid}: {str(prov['error'])[:80]}")
        series[mid] = points
        print(f"  · {mid}: {len(points)} points")
    return series


def _resolved_for_day(
    entry: dict,
    slice_points: list[SeriesPoint],
    *,
    feature_specs: list[dict],
    anomaly_cfg: dict,
    direction_bad: str | None,
) -> ResolvedMetric:
    mid = entry["metric_id"]
    surfaced = set(entry.get("features") or [])
    feats, flags = compute_features(slice_points, feature_specs)
    value = feats["current_value"].value if "current_value" in feats else (
        slice_points[-1].value if slice_points else None
    )

    def _f(name: str) -> float | None:
        return feats[name].value if (name in surfaced and name in feats) else None

    anomaly = None
    if entry.get("anomaly") and entry.get("feature_class") != "windowed_point":
        values = [p.value for p in slice_points if p.value is not None]
        min_points = anomaly_cfg.get("min_points", 14)
        window_days = _parse_days(anomaly_cfg.get("window"), 28)
        z = anomaly_cfg.get("z_threshold", 3.0)
        if len(values) >= min_points + 1:
            windowed = values[-window_days:] if window_days else values
            history, observed = windowed[:-1], windowed[-1]
            r = robust_zscore(history, observed, z_threshold=z)
            anomaly = {
                "observed": r.observed, "expected": r.expected, "expected_range": r.expected_range,
                "deviation_pct": r.deviation_pct, "score": r.score, "direction": r.direction,
                "is_anomaly": r.is_anomaly, "adverse": r.direction == direction_bad,
                "detector": {"strategy": "robust_zscore", "version": "v1", "window": anomaly_cfg.get("window", "28d"), "z_threshold": z},
            }
    return ResolvedMetric(
        metric_id=mid,
        value=value,
        period_delta_pct=_f("period_delta_pct"),
        rolling_mean_7d=_f("rolling_mean_7d"),
        rolling_std_7d=_f("rolling_std_7d"),
        direction_bad=direction_bad,  # type: ignore[arg-type]
        freshness="CURRENT",
        anomaly=anomaly,
        quality_flags=flags,
    )


async def backfill(days: int = 365, *, brand_id: str = DEFAULT_BRAND_ID) -> int:
    from seleric_swarm.bootstrap import build_runtime

    load_dotenv(repo_root() / ".env")
    runtime = build_runtime(Settings())
    if runtime.business_state is None:
        raise RuntimeError("runtime has no business_state service")

    profiles = DomainHealthProfiles()
    loader = ProfileLoader()
    default_profile = loader.get("default_v1")
    feature_specs = default_profile.get("features") or []
    anomaly_cfg = default_profile.get("anomaly") or {}

    today = datetime.now(UTC).date()
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()
    print(f"Fetching {days}d of series ({start} .. {end}) per metric ...")
    series = await _fetch_all_series(runtime, profiles, start=start, end=end, brand_id=brand_id)

    direction_bad = {
        mid: getattr(runtime.metrics.get(mid), "direction_bad", None)
        for mid in series
    }
    # Day axis = union of all fetched dates within range, ascending.
    all_dates = sorted({p.ts for pts in series.values() for p in pts})
    if not all_dates:
        print("No series data returned; nothing to backfill.")
        return 0
    headline_ids = profiles.headline_metrics()
    store = SnapshotStore()
    now_iso = datetime.now(UTC).isoformat()
    written = 0
    print(f"Assembling snapshots for {len(all_dates)} days ...")
    for d in all_dates:
        day_snapshots: list[DomainStateSnapshot] = []
        for domain in ALL_DOMAINS:
            block = profiles.get(domain)
            resolved: list[ResolvedMetric] = []
            status = "OK"
            for entry in block.get("metrics", []):
                mid = entry["metric_id"]
                pts = [p for p in series.get(mid, []) if p.ts <= d]
                if not pts:
                    resolved.append(ResolvedMetric(metric_id=mid, value=None, freshness="UNKNOWN", quality_flags=["MISSING_DATA"]))
                    status = "DEGRADED"
                    continue
                resolved.append(
                    _resolved_for_day(
                        entry, pts, feature_specs=feature_specs, anomaly_cfg=anomaly_cfg,
                        direction_bad=direction_bad.get(mid),
                    )
                )
            snap = DomainStateSnapshot(
                domain=domain, brand_id=brand_id, as_of=d, computed_at=now_iso,
                window={"start": start, "end": d, "grain": "day"}, status=status,
                metrics=resolved, headline_signals=[], provenance={"backfill": True},
            )
            store.save(snap)
            day_snapshots.append(snap)
            written += 1
        if headline_ids:
            headline = build_headline_snapshot(day_snapshots, headline_ids, brand_id=brand_id)
            headline.provenance["backfill"] = True
            store.save(headline)
            written += 1
    print(f"\nBackfilled {written} snapshots ({len(all_dates)} days x {len(ALL_DOMAINS)} domains + business) into {store._base}")
    print(f"Latest business as_of = {all_dates[-1]}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--brand-id", default=DEFAULT_BRAND_ID)
    args = ap.parse_args()
    asyncio.run(backfill(days=args.days, brand_id=args.brand_id))


if __name__ == "__main__":
    main()

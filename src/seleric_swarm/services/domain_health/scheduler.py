from __future__ import annotations

import asyncio
from collections.abc import Sequence

from datetime import UTC, datetime

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.services.domain_health.models import DomainStateSnapshot
from seleric_swarm.services.domain_health.resolver import DomainHealthProfiles, DomainStateResolver
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore
from seleric_swarm.services.mcp_query import DEFAULT_BRAND_ID

# Inventory/procurement/technical excluded -- no MCP module (04 doc).
ALL_DOMAINS = ["commerce", "finance", "performance", "attribution", "funnel", "product", "customer", "operations"]

# The cross-domain ready-store snapshot the fast path reads. Assembled from the
# per-domain snapshots (no re-fetch), saved under this pseudo-domain name.
HEADLINE_DOMAIN = "business"


def build_headline_snapshot(
    snapshots: list[DomainStateSnapshot],
    headline_metric_ids: list[str],
    *,
    brand_id: str = DEFAULT_BRAND_ID,
) -> DomainStateSnapshot:
    """Pick the headline metrics out of already-resolved domain snapshots into
    one `business` snapshot -- the single read the fast path serves "How's the
    business?" from. No MCP calls: the metrics were fetched during the per-
    domain resolve pass. Preserves ``headline_metric_ids`` order.
    """
    by_metric = {m.metric_id: m for s in snapshots for m in s.metrics}
    metrics = [by_metric[mid] for mid in headline_metric_ids if mid in by_metric]
    signals = [sig for s in snapshots for sig in s.headline_signals]
    # UNAVAILABLE if we couldn't gather any headline metric; DEGRADED if any
    # contributing domain snapshot was itself degraded/unavailable.
    if not metrics:
        status = "UNAVAILABLE"
    elif any(s.status != "OK" for s in snapshots):
        status = "DEGRADED"
    else:
        status = "OK"
    as_of = max((s.as_of for s in snapshots), default=datetime.now(UTC).date().isoformat())
    return DomainStateSnapshot(
        domain=HEADLINE_DOMAIN,
        brand_id=brand_id,
        as_of=as_of,
        computed_at=datetime.now(UTC).isoformat(),
        window=snapshots[0].window if snapshots else {},
        status=status,
        metrics=metrics,
        headline_signals=signals,
        provenance={"assembled_from": [s.domain for s in snapshots]},
    )


async def run_once(
    resolver: DomainStateResolver,
    store: SnapshotStore,
    *,
    domains: Sequence[str] = ALL_DOMAINS,
    time_range: TimeRangeV1 | None = None,
    brand_id: str = DEFAULT_BRAND_ID,
) -> list[DomainStateSnapshot]:
    """Resolve + persist one snapshot per domain.

    [Sprint 4 decision] Cron mechanism: no new scheduler dependency and no
    long-running in-process loop -- this is a plain callable an OS-level
    cron (or Windows Task Scheduler) invokes once via `python -m
    seleric_swarm.services.domain_health.scheduler`. Cadence is daily for
    every domain for now; 04 doc's open question (hourly for
    performance/funnel) is deferred until a domain actually needs it --
    re-running this more often is a cron-line change, not a code change.
    Domains resolve sequentially (8 domains, run once/day -- not worth
    asyncio.gather's added complexity unless wall-clock becomes an issue).
    """
    time_range = time_range or TimeRangeV1(kind="relative", relative_token="last_7d")
    snapshots = []
    for domain in domains:
        snapshot = await resolver.resolve(domain, time_range=time_range, brand_id=brand_id, store=store)
        await store.asave(snapshot)
        snapshots.append(snapshot)
    headline_ids = DomainHealthProfiles().headline_metrics()
    if headline_ids:
        headline = build_headline_snapshot(snapshots, headline_ids, brand_id=brand_id)
        await store.asave(headline)
        snapshots.append(headline)
    return snapshots


def main() -> None:
    from seleric_swarm.bootstrap import build_runtime
    from seleric_swarm.config.settings import Settings

    runtime = build_runtime(Settings())
    business_state = runtime.business_state
    if business_state is None:
        raise RuntimeError("build_runtime did not initialize BusinessStateService")
    resolver = DomainStateResolver(business_state)
    snapshots = asyncio.run(run_once(resolver, SnapshotStore()))
    for snapshot in snapshots:
        print(f"{snapshot.domain}: status={snapshot.status} signals={snapshot.headline_signals}")


if __name__ == "__main__":
    main()

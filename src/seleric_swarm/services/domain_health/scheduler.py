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

    [Sprint 4 decision updated] Cron mechanism: run_once now resolves
    domains concurrently using asyncio.gather with a strict timeout so it
    can complete within a 1-minute cadence.
    """
    import logging
    log = logging.getLogger("seleric.business_state.scheduler")

    time_range = time_range or TimeRangeV1(kind="relative", relative_token="last_7d")
    
    async def _resolve_and_save(domain: str) -> DomainStateSnapshot | None:
        try:
            snapshot = await asyncio.wait_for(
                resolver.resolve(domain, time_range=time_range, brand_id=brand_id, store=store),
                timeout=45.0
            )
            await store.asave(snapshot)
            return snapshot
        except Exception as e:
            log.error(f"Failed to resolve snapshot for {domain}", exc_info=e)
            return None

    tasks = [_resolve_and_save(domain) for domain in domains]
    results = await asyncio.gather(*tasks)
    snapshots = [s for s in results if s is not None]

    headline_ids = DomainHealthProfiles().headline_metrics()
    # No domain resolved (e.g. the MCP restarting): an empty headline dated today would shadow the last good one
    # for a day (live 2026-10-09 01:44 UTC: business/2026-10-09.json UNAVAILABLE, read by every mission).
    if headline_ids and snapshots:
        headline = build_headline_snapshot(snapshots, headline_ids, brand_id=brand_id)
        await store.asave(headline)
        snapshots.append(headline)
    return snapshots


async def main() -> None:
    from seleric_swarm.bootstrap import build_runtime
    from seleric_swarm.config.settings import Settings

    runtime = build_runtime(Settings())
    business_state = runtime.business_state
    if business_state is None:
        raise RuntimeError("build_runtime did not initialize BusinessStateService")
    resolver = DomainStateResolver(business_state)
    snapshots = await run_once(resolver, SnapshotStore())
    for snapshot in snapshots:
        print(f"{snapshot.domain}: status={snapshot.status} signals={snapshot.headline_signals}")


if __name__ == "__main__":
    asyncio.run(main())

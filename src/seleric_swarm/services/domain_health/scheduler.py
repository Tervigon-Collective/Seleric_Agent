from __future__ import annotations

import asyncio
from collections.abc import Sequence

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.services.domain_health.models import DomainStateSnapshot
from seleric_swarm.services.domain_health.resolver import DomainStateResolver
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

# Inventory/procurement/technical excluded -- no MCP module (04 doc).
ALL_DOMAINS = ["commerce", "finance", "performance", "attribution", "funnel", "product", "customer", "operations"]


async def run_once(
    resolver: DomainStateResolver,
    store: SnapshotStore,
    *,
    domains: Sequence[str] = ALL_DOMAINS,
    time_range: TimeRangeV1 | None = None,
    brand_id: str = "20",
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
        store.save(snapshot)
        snapshots.append(snapshot)
    return snapshots


def main() -> None:
    from seleric_swarm.bootstrap import build_runtime
    from seleric_swarm.config.settings import Settings

    runtime = build_runtime(Settings())
    resolver = DomainStateResolver(runtime.business_state)
    snapshots = asyncio.run(run_once(resolver, SnapshotStore()))
    for snapshot in snapshots:
        print(f"{snapshot.domain}: status={snapshot.status} signals={snapshot.headline_signals}")


if __name__ == "__main__":
    main()

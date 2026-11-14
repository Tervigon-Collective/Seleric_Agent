from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.exploration.space import default_brand_id
from seleric_swarm.services.domain_health.models import DomainStateSnapshot, ResolvedMetric
from seleric_swarm.services.domain_health.resolver import DomainHealthProfiles, DomainStateResolver
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

# The shipped domains, read from the profile file: adding a domain there is the whole change.
ALL_DOMAINS = DomainHealthProfiles().domain_ids()

# A domain is re-resolved when its snapshot is older than this, or its saved configuration is newer than the snapshot.
# The cron container wakes every minute; without this gate it re-resolved every domain on every wake (about 100 MCP
# queries every 2-3 minutes) although the ready store is documented as hourly.
DEFAULT_MIN_INTERVAL_S = 900

# The cross-domain ready-store snapshot the fast path reads. Assembled from the
# per-domain snapshots (no re-fetch), saved under this pseudo-domain name.
HEADLINE_DOMAIN = "business"


def build_headline_snapshot(
    snapshots: list[DomainStateSnapshot],
    headline_metric_ids: list[str],
    *,
    brand_id: str | None = None,
) -> DomainStateSnapshot:
    """Pick the headline metrics out of already-resolved domain snapshots into
    one `business` snapshot -- the single read the fast path serves "How's the
    business?" from. No MCP calls: the metrics were fetched during the per-
    domain resolve pass. Preserves ``headline_metric_ids`` order.
    """
    brand_id = brand_id or default_brand_id()
    by_metric: dict[str, ResolvedMetric] = {}
    for s in snapshots:  # first domain to name a metric wins, so a repeated id cannot flip between runs
        for m in s.metrics:
            by_metric.setdefault(m.metric_id, m)
    metrics = [by_metric[mid] for mid in headline_metric_ids if mid in by_metric]
    signals = [sig for s in snapshots for sig in s.headline_signals]
    # UNAVAILABLE if we couldn't gather any headline metric; DEGRADED if any
    # contributing domain snapshot was itself degraded/unavailable.
    status: Literal["OK", "DEGRADED", "UNAVAILABLE"]
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


def domains_due(
    profiles: DomainHealthProfiles,
    latest: dict[str, DomainStateSnapshot | None],
    *,
    min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
    now: datetime | None = None,
) -> list[str]:
    now = now or datetime.now(UTC)
    due: list[str] = []
    for domain in profiles.domain_ids():
        if not profiles.get(domain).get("enabled", True):
            continue
        snap = latest.get(domain)
        if snap is None:
            due.append(domain)
            continue
        computed = datetime.fromisoformat(snap.computed_at)
        changed = profiles.override_updated_at(domain)
        if (now - computed).total_seconds() >= min_interval_s or (changed and datetime.fromisoformat(changed) > computed):
            due.append(domain)
    return due


async def run_once(
    resolver: DomainStateResolver,
    store: SnapshotStore,
    *,
    domains: Sequence[str] | None = None,
    time_range: TimeRangeV1 | None = None,
    brand_id: str | None = None,
) -> list[DomainStateSnapshot]:
    """Resolve + persist one snapshot per domain, then rebuild the cross-domain ``business`` headline.

    Domains resolve concurrently, each under a strict timeout. The headline is assembled from the *latest* snapshot of
    every domain, not only the ones resolved in this pass, so a partial pass (only the due domains) cannot shrink it.
    """
    import logging
    log = logging.getLogger("seleric.business_state.scheduler")

    profiles = resolver.profiles
    if domains is None:
        domains = [d for d in profiles.domain_ids() if profiles.get(d).get("enabled", True)]
    time_range = time_range or TimeRangeV1(kind="relative", relative_token="last_7d")
    brand_id = brand_id or default_brand_id()

    async def _resolve_and_save(domain: str) -> DomainStateSnapshot | None:
        try:
            snapshot = await asyncio.wait_for(
                resolver.resolve(domain, time_range=time_range, brand_id=brand_id, store=store),
                timeout=90.0,
            )
            await store.asave(snapshot)
            return snapshot
        except Exception as e:
            log.error(f"Failed to resolve snapshot for {domain}", exc_info=e)
            return None

    results = await asyncio.gather(*[_resolve_and_save(domain) for domain in domains])
    snapshots = [s for s in results if s is not None]

    headline_ids = profiles.headline_metrics()
    # No domain resolved (e.g. the MCP restarting): an empty headline dated today would shadow the last good one
    # for a day (live 2026-10-09 01:44 UTC: business/2026-10-09.json UNAVAILABLE, read by every mission).
    if headline_ids and snapshots:
        fresh = {s.domain: s for s in snapshots}
        live = [d for d in profiles.domain_ids() if profiles.get(d).get("enabled", True)]  # a switched-off domain's old numbers must not feed the headline
        everything = [fresh[d] if d in fresh else await store.aget_latest(d) for d in live]
        headline = build_headline_snapshot([s for s in everything if s is not None], headline_ids, brand_id=brand_id)
        await store.asave(headline)
        snapshots.append(headline)
    return snapshots


async def main(argv: Sequence[str] | None = None) -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="resolve every enabled domain regardless of age")
    args = parser.parse_args(argv)

    profiles = DomainHealthProfiles.live()
    store = SnapshotStore()
    interval = float(os.getenv("BUSINESS_STATE_MIN_INTERVAL_S", DEFAULT_MIN_INTERVAL_S) or DEFAULT_MIN_INTERVAL_S)
    latest = {d: await store.aget_latest(d) for d in profiles.domain_ids()}
    due = [d for d in profiles.domain_ids() if profiles.get(d).get("enabled", True)] if args.force else \
        domains_due(profiles, latest, min_interval_s=interval)
    if not due:  # checked before the runtime is built: the common case costs nothing
        print('{"status": "skipped", "reason": "not_due"}')
        return

    from seleric_swarm.bootstrap import build_runtime
    from seleric_swarm.config.settings import Settings

    runtime = build_runtime(Settings())
    business_state = runtime.business_state
    if business_state is None:
        raise RuntimeError("build_runtime did not initialize BusinessStateService")
    resolver = DomainStateResolver(business_state, profiles)
    snapshots = await run_once(resolver, store, domains=due)
    for snapshot in snapshots:
        print(f"{snapshot.domain}: status={snapshot.status} signals={snapshot.headline_signals}")


if __name__ == "__main__":
    asyncio.run(main())

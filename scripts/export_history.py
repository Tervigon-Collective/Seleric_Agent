"""Export daily metric history to CSV for scripts.backtest_forecasters.

    uv run python -m scripts.export_history [--days 365] [--brand-id 20] [--out history.csv]

Reuses the live ``fetch_series`` path (Cube via MCP) and the domain-health profiles
to resolve each metric's agent_id. Output columns: date,metric,value.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from seleric_swarm.config.settings import Settings
from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.paths import repo_root
from seleric_swarm.services.business_state.series import fetch_series
from seleric_swarm.services.domain_health.resolver import DomainHealthProfiles
from seleric_swarm.services.domain_health.scheduler import ALL_DOMAINS
from seleric_swarm.services.mcp_query import DEFAULT_BRAND_ID

TARGETS = ("metric.net_sales", "metric.orders", "metric.sessions", "metric.spend")


def _agent_ids(profiles: DomainHealthProfiles) -> dict[str, str]:
    out: dict[str, str] = {}
    for domain in ALL_DOMAINS:
        block = profiles.get(domain)
        for entry in block.get("metrics", []):
            out.setdefault(entry["metric_id"], block.get("agent_id", f"{domain}_agent"))
    return out


async def export(days: int, brand_id: str, out: Path) -> int:
    from seleric_swarm.bootstrap import build_runtime

    load_dotenv(repo_root() / ".env")
    runtime = build_runtime(Settings())
    agents = _agent_ids(DomainHealthProfiles())

    today = datetime.now(UTC).date()
    start, end = (today - timedelta(days=days)).isoformat(), today.isoformat()
    tr = TimeRangeV1(kind="absolute", start=start, end=end)
    lookback = (date.fromisoformat(end) - date.fromisoformat(start)).days + 5

    rows: list[tuple[str, str, float]] = []
    for mid in TARGETS:
        definition = runtime.metrics.get(mid)
        if definition is None:
            print(f"  ! {mid}: not in metric registry, skipping")
            continue
        try:
            points, prov = await fetch_series(
                runtime, definition=definition, agent_id=agents.get(mid, "analytics_agent"),
                time_range=tr, brand_id=brand_id, grain="day", max_lookback_days=lookback,
            )
        except Exception as exc:
            print(f"  ! {mid}: fetch failed ({type(exc).__name__}: {str(exc)[:80]})")
            continue
        if prov.get("error"):
            print(f"  ! {mid}: {str(prov['error'])[:80]}")
        print(f"  · {mid}: {len(points)} points")
        rows.extend((str(p.ts), mid, p.value) for p in points if p.value is not None)

    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "metric", "value"])
        w.writerows(sorted(rows))
    print(f"Wrote {len(rows)} rows to {out}")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--brand-id", default=DEFAULT_BRAND_ID)
    ap.add_argument("--out", type=Path, default=Path("history.csv"))
    a = ap.parse_args()
    asyncio.run(export(a.days, str(a.brand_id), a.out))


if __name__ == "__main__":
    main()

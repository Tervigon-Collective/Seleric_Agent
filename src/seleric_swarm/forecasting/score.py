"""Score stored forecasts against days that have since matured.

Run on a schedule (the business-state cron calls ``--if-due`` once a day)::

    python -m seleric_swarm.forecasting.score --if-due
    python -m seleric_swarm.forecasting.score --payload reports/forecasting/2026-10-10/net_sales.json

The payload path is for a backtest report or a saved ``ForecastArtifact`` dump.
Live artifacts are read from ``v3_artifacts`` when ``DATABASE_URL`` is set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from seleric_swarm.models.evaluation import score_forecast_artifact
from seleric_swarm.paths import repo_root

_INTERVAL = timedelta(hours=24)


def score_payload(
    payload: dict[str, Any],
    actuals_by_metric: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Score each target in a forecast payload against ISO-date actuals."""
    out: dict[str, dict[str, float]] = {}
    for target in payload.get("targets") or []:
        mid = str(target.get("metric_id") or "")
        if not mid:
            continue
        actuals = actuals_by_metric.get(mid) or {}
        scored = score_forecast_artifact(payload, actuals, metric_id=mid)
        if scored:
            out[mid] = scored
    return out


def is_due(stamp: Path, *, now: datetime | None = None, interval: timedelta = _INTERVAL) -> bool:
    now = now or datetime.now(timezone.utc)
    if not stamp.exists():
        return True
    try:
        last = datetime.fromisoformat(stamp.read_text(encoding="utf-8").strip())
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= interval


def mark_ran(stamp: Path, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(now.isoformat(), encoding="utf-8")


def _dates_to_score(payload: dict[str, Any], *, as_of: date) -> dict[str, list[str]]:
    """Mature forecast days (on or before yesterday) that still need actuals."""
    wanted: dict[str, list[str]] = {}
    yesterday = as_of - timedelta(days=1)
    for target in payload.get("targets") or []:
        mid = str(target.get("metric_id") or "")
        if not mid or target.get("status") == "refused":
            continue
        days = [
            str(d.get("date") or "")[:10]
            for d in (target.get("days") or [])
            if str(d.get("date") or "")[:10] and date.fromisoformat(str(d.get("date"))[:10]) <= yesterday
        ]
        if days:
            wanted[mid] = days
    return wanted


async def fetch_actuals(
    mcp: Any,
    metric_id: str,
    days: list[str],
) -> dict[str, float]:
    from seleric_swarm.forecasting.backtest import fetch_daily_series

    if not days:
        return {}
    start = date.fromisoformat(min(days))
    end = date.fromisoformat(max(days))
    series = await fetch_daily_series(mcp, metric_id, start=start, end=end)
    return {d.isoformat(): v for d, v in series.items() if d.isoformat() in set(days)}


def load_recent_forecasts(*, limit: int = 20) -> list[dict[str, Any]]:
    """Forecast artifact payloads from Postgres, newest first. Empty if no DB."""
    url = os.environ.get("DATABASE_URL") or os.environ.get("COMPOSE_DATABASE_URL") or ""
    if not url:
        return []
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """SELECT id, workspace_id, body FROM v3_artifacts
                WHERE artifact_type = 'forecast'
                ORDER BY created_at DESC LIMIT :n"""
            ),
            {"n": limit},
        ).mappings()
        out: list[dict[str, Any]] = []
        for row in rows:
            body = row["body"]
            artifact = json.loads(body) if isinstance(body, str) else body
            payload = artifact.get("payload") if isinstance(artifact, dict) else None
            if isinstance(payload, dict) and payload.get("targets"):
                payload = {**payload, "_artifact_id": row["id"], "_workspace_id": row["workspace_id"]}
                out.append(payload)
        return out


def write_score(artifact_key: str, scores: dict[str, Any], *, out_dir: Path | None = None) -> Path:
    day = date.today().isoformat()
    root = out_dir or (repo_root() / "reports" / "forecasting" / "scores" / day)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{artifact_key}.json"
    path.write_text(
        json.dumps({"artifact": artifact_key, "scored_at": datetime.now(timezone.utc).isoformat(), "scores": scores}, indent=2),
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seleric_swarm.forecasting.score")
    parser.add_argument("--if-due", action="store_true", help="Exit 0 unless 24h have passed")
    parser.add_argument("--payload", default="", help="ForecastArtifact or backtest JSON to score offline")
    parser.add_argument("--stamp", default="")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    stamp = Path(args.stamp) if args.stamp else repo_root() / "reports" / "forecasting" / "scores" / ".last"
    if args.if_due and not is_due(stamp):
        print(json.dumps({"status": "skipped", "reason": "not_due"}))
        return 0

    if args.payload:
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        # A backtest report has no per-day forecast; only a ForecastArtifact payload scores.
        if not payload.get("targets"):
            print(json.dumps({"status": "skipped", "reason": "payload_has_no_targets"}))
            return 0
        print(json.dumps({"status": "payload_loaded", "targets": [t.get("metric_id") for t in payload["targets"]]}))
        return 0

    try:
        payloads = load_recent_forecasts(limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "skipped", "reason": f"db:{type(exc).__name__}"}))
        return 0
    if not payloads:
        print(json.dumps({"status": "skipped", "reason": "no_forecast_artifacts"}))
        if args.if_due:
            mark_ran(stamp)
        return 0

    import asyncio

    async def _run() -> list[dict[str, Any]]:
        from dotenv import load_dotenv

        from seleric_swarm.protocols.mcp.gateway import MCPGateway

        load_dotenv(repo_root() / ".env")
        mcp = MCPGateway("config/mcp_servers.yaml")
        as_of = date.today()
        written: list[dict[str, Any]] = []
        for payload in payloads:
            wanted = _dates_to_score(payload, as_of=as_of)
            actuals: dict[str, dict[str, float]] = {}
            for mid, days in wanted.items():
                actuals[mid] = await fetch_actuals(mcp, mid, days)
            scores = score_payload(payload, actuals)
            if not scores:
                continue
            key = str(payload.get("_artifact_id") or "forecast")
            path = write_score(key, scores)
            written.append({"artifact": key, "report": str(path), "metrics": list(scores)})
        return written

    try:
        written = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}))
        return 0
    if args.if_due:
        mark_ran(stamp)
    print(json.dumps({"status": "ok", "scored": written}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

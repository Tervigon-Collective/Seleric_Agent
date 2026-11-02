"""Evaluate brand targets against the semantic layer's series and record the result in Postgres (schema ``monitor``).

    python -m seleric_swarm.services.monitoring.runner --brand 20 [--as-of YYYY-MM-DD] [--backfill-days N] [--dry-run]

Reads: monitor.metrics, monitor.targets_current, monitor.known_periods. Writes: monitor.evaluations, monitor.findings,
monitor.finding_events. Never touches ClickHouse; the series come through the semantic layer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import Connection, create_engine, text

from seleric_swarm.config.settings import get_settings
from seleric_swarm.paths import repo_root
from seleric_swarm.services.mcp_query import row_date
from seleric_swarm.services.monitoring.evaluate import (
    Band,
    DataEvent,
    Evaluation,
    GateConfig,
    MetricDef,
    Series,
    data_events,
    evaluate,
    untrusted_days,
)

AGENT_ID = "monitoring"
MAX_PARALLEL = 5
EVENT_WINDOW_DAYS = 6  # data events newer than this stay open
PHRASE = {
    "below_floor": "below the floor",
    "below_target": "below the target",
    "above_target": "above the target",
    "above_ceiling": "above the ceiling",
}


@dataclass(frozen=True)
class Loaded:
    metric: MetricDef
    label: str
    band: Band | None
    target_version_id: int | None


@dataclass(frozen=True)
class Desired:
    finding_id: str
    metric_id: str | None
    detector: str
    severity: str
    summary: str


def load_metrics(conn: Connection, brand_id: int) -> list[Loaded]:
    rows = conn.execute(
        text(
            """SELECT m.metric_id, m.label, m.direction_bad, m.eval_window, m.kind, m.numerator_id, m.denominator_id,
                      m.weight_id, m.settles_after_days, t.id AS tv, t.floor_value, t.target_value, t.ceiling_value
               FROM monitor.metrics m
               LEFT JOIN monitor.targets_current t ON t.metric_id = m.metric_id AND t.brand_id = :b
               WHERE m.enabled ORDER BY m.metric_id"""
        ),
        {"b": brand_id},
    ).mappings().all()
    out = []
    for r in rows:
        metric = MetricDef(r["metric_id"], r["direction_bad"], r["eval_window"], r["kind"], r["numerator_id"],
                           r["denominator_id"], r["weight_id"], r["settles_after_days"])
        band = None if r["tv"] is None else Band(r["floor_value"], r["target_value"], r["ceiling_value"])
        out.append(Loaded(metric, r["label"], band, r["tv"]))
    return out


def load_known_days(conn: Connection, brand_id: int) -> dict[str, str]:
    rows = conn.execute(
        text("SELECT start_day, end_day, reason FROM monitor.known_periods WHERE brand_id = :b"), {"b": brand_id}
    ).mappings().all()
    known: dict[str, str] = {}
    for r in rows:
        d = r["start_day"]
        while d <= r["end_day"]:
            known[d.isoformat()] = r["reason"]
            d += timedelta(days=1)
    return known


async def fetch_series(runtime: Any, metric_ids: list[str], start: str, end: str, brand_id: int) -> Series:
    from seleric_swarm.toolsets._data import raw_query_metric

    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def one(mid: str) -> tuple[str, dict[str, float]]:
        async with sem:
            r = await raw_query_metric(
                runtime.mcp, agent_id=AGENT_ID, metric_id=mid, start=start, end=end, grain="day",
                filters=[{"dimension": "brand_id", "operator": "equals", "values": [str(brand_id)]}],
            )
        if r.get("error"):
            raise RuntimeError(f"{mid}: {r['error']}")  # a failed fetch must never read as 'no data'
        points: dict[str, float] = {}
        for row in r.get("rows") or []:
            day, value = row_date(row), row.get(mid)
            if day is not None and value is not None:
                points[str(day)[:10]] = float(value)
        return mid, points

    return dict(await asyncio.gather(*[one(m) for m in metric_ids]))


def _num(x: float) -> str:
    return f"{x:,.4g}" if abs(x) < 1000 else f"{x:,.0f}"


def summary_of(item: Loaded, e: Evaluation) -> str:
    band = item.band
    line = {"below_floor": band.floor, "below_target": band.target, "above_target": band.target, "above_ceiling": band.ceiling}
    ref = line.get(e.status) if band else None
    ref_text = "" if ref is None else f" {_num(ref)}"
    prov = " (provisional)" if e.data_status == "provisional" else ""
    return f"{item.label} on {e.day} is {_num(e.observed or 0)}, {PHRASE.get(e.status, e.status)}{ref_text}{prov}."


def desired_findings(
    brand_id: int, items: list[Loaded], evals: list[Evaluation], events: list[DataEvent], as_of: str, known: dict[str, str]
) -> list[Desired]:
    """What should be open right now: breaches on the latest day, and recent data events. Pure."""
    by_id = {i.metric.metric_id: i for i in items}
    out = [
        Desired(f"{brand_id}:{e.metric_id}:target_gap", e.metric_id, "target_gap", e.severity, summary_of(by_id[e.metric_id], e))
        for e in evals
        if e.day == as_of and e.severity in ("critical", "warning")
    ]
    cutoff = (date.fromisoformat(as_of) - timedelta(days=EVENT_WINDOW_DAYS)).isoformat()
    for ev in events:
        if ev.day < cutoff or ev.day > as_of:
            continue
        why = f" Explained: {known[ev.day]}." if ev.day in known else ""
        out.append(Desired(f"{brand_id}:data_event:{ev.kind}:{ev.day}", None, f"data_event:{ev.kind}", "info", f"{ev.kind} on {ev.day}: {ev.detail}.{why}"))
    return out


def reconcile(desired: list[Desired], existing: dict[str, tuple[str, str]]) -> list[tuple[str, Desired | str, str]]:
    """(op, finding, note) with op in insert / update / reopen / resolve. ``existing`` maps id -> (state, severity). Pure."""
    ops: list[tuple[str, Desired | str, str]] = []
    want = {d.finding_id: d for d in desired}
    for fid, d in want.items():
        if fid not in existing:
            ops.append(("insert", d, "opened"))
        elif existing[fid][0] == "resolved":
            ops.append(("reopen", d, "reopened"))
        else:
            ops.append(("update", d, "severity changed" if existing[fid][1] != d.severity else ""))
    ops += [("resolve", fid, "no longer breached") for fid, (state, _) in existing.items() if fid not in want and state != "resolved"]
    return ops


def write(conn: Connection, brand_id: int, items: list[Loaded], evals: list[Evaluation], desired: list[Desired], tz: ZoneInfo) -> dict[str, int]:
    by_id = {i.metric.metric_id: i for i in items}
    for e in evals:
        item = by_id[e.metric_id]
        start = datetime.combine(date.fromisoformat(e.day), datetime.min.time(), tzinfo=tz)
        band = item.band
        conn.execute(
            text(
                """INSERT INTO monitor.evaluations(brand_id, metric_id, grain, period_start, observed, target_version_id,
                       status, data_status, details)
                   VALUES (:b, :m, 'day', :ps, :obs, :tv, :st, :ds, CAST(:d AS jsonb))"""
            ),
            {"b": brand_id, "m": e.metric_id, "ps": start, "obs": e.observed, "tv": item.target_version_id,
             "st": e.status, "ds": e.data_status,
             "d": json.dumps({"days_used": e.days_used, "reason": e.reason, "explained_by": e.explained_by, "severity": e.severity,
                              "band": None if band is None else [band.floor, band.target, band.ceiling]})},
        )
    rows = conn.execute(
        text("SELECT finding_id, state, severity FROM monitor.findings "
             "WHERE brand_id = :b AND (detector = 'target_gap' OR detector LIKE 'data_event:%')"), {"b": brand_id}
    ).all()
    existing = {r[0]: (r[1], r[2]) for r in rows}
    counts = {"insert": 0, "update": 0, "reopen": 0, "resolve": 0}
    for op, what, note in reconcile(desired, existing):
        counts[op] += 1
        if isinstance(what, str):
            conn.execute(text("UPDATE monitor.findings SET state='resolved', resolved_at=NOW(), updated_at=NOW() WHERE finding_id=:f"), {"f": what})
            fid, new_state = what, "resolved"
        else:
            fid = what.finding_id
            new_state = "open" if op in ("insert", "reopen") else "ongoing"
            conn.execute(
                text(
                    """INSERT INTO monitor.findings(finding_id, brand_id, metric_id, detector, state, severity, summary)
                       VALUES (:f, :b, :m, :d, :s, :sev, :sum)
                       ON CONFLICT (finding_id) DO UPDATE SET state=:s, severity=:sev, summary=:sum, updated_at=NOW(),
                           resolved_at=NULL"""
                ),
                {"f": fid, "b": brand_id, "m": what.metric_id, "d": what.detector, "s": new_state, "sev": what.severity, "sum": what.summary},
            )
        if op != "update" or note:
            conn.execute(
                text("INSERT INTO monitor.finding_events(finding_id, from_state, to_state, note) VALUES (:f, :from, :to, :n)"),
                {"f": fid, "from": existing.get(fid, (None,))[0], "to": new_state, "n": note},
            )
    return counts


async def run(brand_id: int, as_of: str | None, backfill_days: int, dry_run: bool) -> dict[str, Any]:
    from seleric_swarm.bootstrap import build_runtime
    from seleric_swarm.config.settings import Settings

    cfg_path = repo_root() / "config/monitoring.yaml"
    tz = ZoneInfo(yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8"))["timezone"])
    gate = GateConfig.load()
    as_of = as_of or (datetime.now(tz).date() - timedelta(days=1)).isoformat()
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        items, known = load_metrics(conn, brand_id), load_known_days(conn, brand_id)
    ids = sorted({p for i in items for p in i.metric.part_ids}
                 | {gate.return_numerator, gate.return_denominator, gate.spend_metric})
    lookback = gate.spend_lookback_days + gate.window_days + backfill_days + 2
    start = (date.fromisoformat(as_of) - timedelta(days=lookback)).isoformat()
    series = await fetch_series(build_runtime(Settings()), ids, start, as_of, brand_id)
    events = data_events(series, gate)
    bad = untrusted_days(events)
    days = [(date.fromisoformat(as_of) - timedelta(days=i)).isoformat() for i in range(backfill_days, -1, -1)]
    evals = [evaluate(i.metric, i.band, series, d, untrusted=bad, gate=gate, as_of=as_of, explained=known) for i in items for d in days]
    desired = desired_findings(brand_id, items, evals, events, as_of, known)
    result: dict[str, Any] = {"as_of": as_of, "metrics": len(items), "evaluations": len(evals), "desired_findings": len(desired),
                              "by_severity": {s: sum(1 for e in evals if e.day == as_of and e.severity == s) for s in ("critical", "warning", "info")}}
    if dry_run:
        result["findings"] = [d.summary for d in desired]
        return result
    with engine.begin() as conn:
        result["written"] = write(conn, brand_id, items, evals, desired, tz)
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--brand", type=int, required=True)
    p.add_argument("--as-of")
    p.add_argument("--backfill-days", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    print(json.dumps(asyncio.run(run(a.brand, a.as_of, a.backfill_days, a.dry_run)), indent=2, default=str))


if __name__ == "__main__":
    main()

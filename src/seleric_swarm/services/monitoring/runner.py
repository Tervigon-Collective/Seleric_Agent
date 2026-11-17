"""Evaluate brand targets against the semantic layer's series and record the result in Postgres (schema ``monitor``).

    python -m seleric_swarm.services.monitoring.runner --brand 20 [--as-of YYYY-MM-DD] [--backfill-days N] [--dry-run]

Reads: monitor.metrics, monitor.targets_current, monitor.known_periods. Writes: monitor.evaluations, monitor.findings,
monitor.finding_events. Never touches ClickHouse; the series come through the semantic layer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
PUSHGATEWAY_URL = os.getenv("MONITORING_PUSHGATEWAY_URL", "http://172.17.0.1:9091").rstrip("/")
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2, "none": 3}
_log = logging.getLogger("seleric.monitoring.runner")
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
    definitions: dict[str, str] | None = None  # part id -> catalogue description when the target was seeded


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
                      m.weight_id, m.settles_after_days, t.id AS tv, t.floor_value, t.target_value, t.ceiling_value, m.definitions
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
        out.append(Loaded(metric, r["label"], band, r["tv"], r["definitions"]))
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


async def fetch_series(runtime: Any, metric_ids: list[str], start: str, end: str, brand_id: int) -> tuple[Series, dict[str, str]]:
    """(series, failed). A failed metric is reported in ``failed`` and never reads as 'no data'; the others still load."""
    from seleric_swarm.toolsets._data import raw_query_metric

    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def one(mid: str) -> tuple[str, dict[str, float] | str]:
        try:
            async with sem:
                r = await raw_query_metric(
                    runtime.mcp, agent_id=AGENT_ID, metric_id=mid, start=start, end=end, grain="day",
                    filters=[{"dimension": "brand_id", "operator": "equals", "values": [str(brand_id)]}],
                )
        except Exception as exc:
            return mid, f"{type(exc).__name__}: {exc}"
        if r.get("error"):
            return mid, str(r["error"])
        points: dict[str, float] = {}
        for row in r.get("rows") or []:
            day, value = row_date(row), row.get(mid)
            if day is not None and value is not None:
                points[str(day)[:10]] = float(value)
        return mid, points

    series: Series = {}
    failed: dict[str, str] = {}
    for mid, got in await asyncio.gather(*[one(m) for m in metric_ids]):
        if isinstance(got, str):
            failed[mid] = got
        else:
            series[mid] = got
    return series, failed


def default_as_of(now: datetime, day_complete_hour: int) -> str:
    """Yesterday once it has finished loading, else the day before."""
    # shortcut: clock rule from measured load times (yesterday settles 1-7 h after IST midnight); upgrade to the
    # semantic layer's freshness watermark if loads drift later.
    return (now.date() - timedelta(days=1 if now.hour >= day_complete_hour else 2)).isoformat()


def judged_days(metric: MetricDef, as_of: str, backfill_days: int = 0) -> list[str]:
    """``as_of`` (and any backfill), plus the day that has just settled for a late-settling metric."""
    end = date.fromisoformat(as_of)
    offsets = set(range(backfill_days + 1)) | ({metric.settles_after_days} if metric.settles_after_days > 0 else set())
    return sorted((end - timedelta(days=o)).isoformat() for o in offsets)


def changed_definitions(items: list[Loaded], live: dict[str, str]) -> dict[str, list[str]]:
    """metric id -> parts whose catalogue description differs from the one stored when the target was seeded."""
    out: dict[str, list[str]] = {}
    for i in items:
        stored = i.definitions or {}
        parts = [p for p in i.metric.part_ids if p in stored and p in live and stored[p] != live[p]]
        if parts:
            out[i.metric.metric_id] = parts
    return out


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
    alert_days = {mid: set(judged_days(i.metric, as_of)) for mid, i in by_id.items()}
    worst: dict[str, Evaluation] = {}
    for e in sorted(evals, key=lambda e: e.day, reverse=True):  # newest first, so a tie keeps the newer day
        bad = e.severity in ("critical", "warning") and e.day in alert_days.get(e.metric_id, ())
        if bad and (e.metric_id not in worst or SEVERITY_RANK[e.severity] < SEVERITY_RANK[worst[e.metric_id].severity]):
            worst[e.metric_id] = e
    out = [
        Desired(f"{brand_id}:{mid}:target_gap", mid, "target_gap", e.severity, summary_of(by_id[mid], e))
        for mid, e in sorted(worst.items())
    ]
    cutoff = (date.fromisoformat(as_of) - timedelta(days=EVENT_WINDOW_DAYS)).isoformat()
    for ev in events:
        if ev.day < cutoff or ev.day > as_of:
            continue
        why = f" Explained: {known[ev.day]}." if ev.day in known else ""
        out.append(Desired(f"{brand_id}:data_event:{ev.kind}:{ev.day}", None, f"data_event:{ev.kind}", "info", f"{ev.kind} on {ev.day}: {ev.detail}.{why}"))
    return out


def reconcile(
    desired: list[Desired], existing: dict[str, tuple[str, str]], frozen: set[str] | frozenset[str] = frozenset()
) -> list[tuple[str, Desired | str, str]]:
    """(op, finding, note) with op in insert / update / reopen / resolve. ``existing`` maps id -> (state, severity).
    ``frozen`` ids (their data failed to load this run) are left exactly as they are. Pure."""
    ops: list[tuple[str, Desired | str, str]] = []
    want = {d.finding_id: d for d in desired if d.finding_id not in frozen}
    existing = {k: v for k, v in existing.items() if k not in frozen}
    for fid, d in want.items():
        if fid not in existing:
            ops.append(("insert", d, "opened"))
        elif existing[fid][0] == "resolved":
            ops.append(("reopen", d, "reopened"))
        else:
            ops.append(("update", d, "severity changed" if existing[fid][1] != d.severity else ""))
    ops += [("resolve", fid, "no longer breached") for fid, (state, _) in existing.items() if fid not in want and state != "resolved"]
    return ops


def write(
    conn: Connection, brand_id: int, items: list[Loaded], evals: list[Evaluation], desired: list[Desired], tz: ZoneInfo,
    *, frozen_metrics: set[str] = frozenset(), frozen_events: set[str] = frozenset(), new_definitions: dict[str, dict[str, str]] | None = None,
) -> dict[str, int]:
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
    for mid, defs in (new_definitions or {}).items():
        conn.execute(text("UPDATE monitor.metrics SET definitions = CAST(:d AS jsonb) WHERE metric_id = :m AND definitions IS NULL"),
                     {"m": mid, "d": json.dumps(defs)})
    rows = conn.execute(
        text("SELECT finding_id, state, severity, metric_id, detector FROM monitor.findings WHERE brand_id = :b "
             "AND (detector IN ('target_gap', 'definition_changed') OR detector LIKE 'data_event:%')"), {"b": brand_id}
    ).all()
    existing = {r[0]: (r[1], r[2]) for r in rows}
    frozen = {r[0] for r in rows if r[3] in frozen_metrics or r[4] in {f"data_event:{k}" for k in frozen_events}}
    counts = {"insert": 0, "update": 0, "reopen": 0, "resolve": 0}
    for op, what, note in reconcile(desired, existing, frozen):
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
                           resolved_at=NULL,
                           opened_at=CASE WHEN monitor.findings.state = 'resolved' THEN NOW() ELSE monitor.findings.opened_at END"""
                ),
                {"f": fid, "b": brand_id, "m": what.metric_id, "d": what.detector, "s": new_state, "sev": what.severity, "sum": what.summary},
            )
        if op != "update" or note:
            conn.execute(
                text("INSERT INTO monitor.finding_events(finding_id, from_state, to_state, note) VALUES (:f, :from, :to, :n)"),
                {"f": fid, "from": existing.get(fid, (None,))[0], "to": new_state, "n": note},
            )
    return counts


def open_findings(conn: Connection, brand_id: int) -> list[tuple[str, str, str]]:
    """(metric or detector, detector, severity) of every open critical / warning finding, for the alert push."""
    rows = conn.execute(
        text("SELECT COALESCE(metric_id, finding_id), detector, severity FROM monitor.findings "
             "WHERE brand_id = :b AND state <> 'resolved' AND severity IN ('critical', 'warning') ORDER BY 1"), {"b": brand_id}
    ).all()
    return [(r[0], r[1], r[2]) for r in rows]


async def run(brand_id: int, as_of: str | None, backfill_days: int, dry_run: bool) -> dict[str, Any]:
    from seleric_swarm.bootstrap import build_runtime
    from seleric_swarm.config.settings import Settings

    cfg = yaml.safe_load((repo_root() / "config/monitoring.yaml").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg["timezone"])
    gate = GateConfig.load()
    as_of = as_of or default_as_of(datetime.now(tz), int(cfg.get("day_complete_hour", 0)))
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        items, known = load_metrics(conn, brand_id), load_known_days(conn, brand_id)
    gate_ids = {gate.return_numerator, gate.return_denominator, gate.spend_metric}
    ids = sorted({p for i in items for p in i.metric.part_ids} | gate_ids)
    settle = max((i.metric.settles_after_days for i in items), default=0)
    lookback = gate.spend_lookback_days + gate.window_days + max(backfill_days, settle) + 2
    start = (date.fromisoformat(as_of) - timedelta(days=lookback)).isoformat()
    runtime = build_runtime(Settings())
    series, failed = await fetch_series(runtime, ids, start, as_of, brand_id)

    # Catalogue descriptions (they carry the formula) guard against an id that keeps its name but changes meaning (D-194).
    live: dict[str, str] = {}
    try:
        await runtime.bootstrap.refresh_if_stale()
        for mid in ids:
            d = runtime.metrics.get(mid)
            if d is not None and (d.raw or {}).get("description"):
                live[mid] = str(d.raw["description"])
    except Exception:
        _log.warning("catalogue not loaded; definition guard skipped", exc_info=True)
    changed = changed_definitions(items, live)
    new_definitions = {
        i.metric.metric_id: {p: live[p] for p in i.metric.part_ids}
        for i in items if i.definitions is None and all(p in live for p in i.metric.part_ids)
    }

    # A missing input yields no event of its kind (data_events never guesses); that kind's open findings stay as they are.
    frozen_events = ({"bulk_return"} if {gate.return_numerator, gate.return_denominator} & failed.keys() else set()) | (
        {"spend_collapse"} if gate.spend_metric in failed else set())
    events = data_events(series, gate)
    bad = untrusted_days(events)
    frozen_metrics = {i.metric.metric_id for i in items if set(i.metric.part_ids) & failed.keys()}
    if {gate.return_numerator, gate.return_denominator} & failed.keys():  # no bulk-return gate: late-settling days can't be trusted
        frozen_metrics |= {i.metric.metric_id for i in items if i.metric.settles_after_days > 0}
    evals: list[Evaluation] = []
    for i in items:
        mid = i.metric.metric_id
        for d in judged_days(i.metric, as_of, backfill_days):
            if mid in frozen_metrics:
                why = "; ".join(f"{p}: {failed[p]}" for p in i.metric.part_ids if p in failed) or "trust-gate inputs failed"
                evals.append(Evaluation(mid, d, None, "no_data", "none", 0, "stale", f"fetch failed: {why}"))
            elif mid in changed:
                evals.append(Evaluation(mid, d, None, "untrusted", "none", 0, "fresh",
                                        f"catalogue definition of {', '.join(changed[mid])} changed since the target was seeded"))
            else:
                evals.append(evaluate(i.metric, i.band, series, d, untrusted=bad, gate=gate, as_of=as_of, explained=known))
    desired = desired_findings(brand_id, items, evals, events, as_of, known)
    labels = {i.metric.metric_id: i.label for i in items}
    desired += [
        Desired(f"{brand_id}:{mid}:definition_changed", mid, "definition_changed", "warning",
                f"{labels[mid]}: the catalogue definition of {', '.join(parts)} changed since its target was seeded; "
                "not judged until the target is re-seeded and monitor.metrics.definitions is cleared.")
        for mid, parts in sorted(changed.items()) if mid not in frozen_metrics
    ]
    result: dict[str, Any] = {"as_of": as_of, "metrics": len(items), "evaluations": len(evals), "desired_findings": len(desired),
                              "failed_metrics": failed, "definition_changed": changed,
                              "by_severity": {s: sum(1 for d in desired if d.severity == s) for s in ("critical", "warning", "info")}}
    if dry_run:
        result["findings"] = [d.summary for d in desired]
        return result
    with engine.begin() as conn:
        result["written"] = write(conn, brand_id, items, evals, desired, tz, frozen_metrics=frozen_metrics,
                                  frozen_events=frozen_events, new_definitions=new_definitions)
        result["open_findings"] = open_findings(conn, brand_id)
    return result


def _label(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def push(brand_id: int, result: dict[str, Any] | None) -> None:
    """Run health (POST: a failed run keeps the last success time) and open findings (PUT: resolved ones disappear)."""
    ok = result is not None and not result.get("failed_metrics")
    health = [f"agent_monitoring_run_success {int(ok)}",
              f"agent_monitoring_metrics_failed {len((result or {}).get('failed_metrics') or {})}"]
    if ok:
        health.append(f"agent_monitoring_last_success_timestamp_seconds {time.time():.0f}")
    bodies = [("POST", f"{PUSHGATEWAY_URL}/metrics/job/agent_monitoring/brand/{brand_id}", health)]
    if result is not None and "open_findings" in result:
        lines = [f'agent_monitoring_finding_open{{metric="{_label(m)}",detector="{_label(d)}",severity="{s}"}} 1'
                 for m, d, s in result["open_findings"]]
        bodies.append(("PUT", f"{PUSHGATEWAY_URL}/metrics/job/agent_monitoring_findings/brand/{brand_id}", lines))
    for method, url, lines in bodies:
        try:
            req = urllib.request.Request(url, data=("\n".join(lines) + "\n").encode(), method=method)
            urllib.request.urlopen(req, timeout=10).close()
        except Exception:
            _log.warning("pushgateway push failed: %s", url, exc_info=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--brand", type=int, required=True)
    p.add_argument("--as-of")
    p.add_argument("--backfill-days", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    result: dict[str, Any] | None = None
    try:
        result = asyncio.run(run(a.brand, a.as_of, a.backfill_days, a.dry_run))
        print(json.dumps(result, indent=2, default=str))
    finally:
        if not a.dry_run:
            push(a.brand, result)
    if result.get("failed_metrics"):
        sys.exit(1)


if __name__ == "__main__":
    main()

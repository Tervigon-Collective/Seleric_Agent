"""Read side of the monitor schema: what the app shows. No metric names; labels, bands and states come from Postgres."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, text

SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def shape_state(findings: list[dict[str, Any]], latest: list[dict[str, Any]], known: list[dict[str, Any]]) -> dict[str, Any]:
    """Findings worst first, newest first within a severity; resolved ones are kept apart so the list never grows without limit."""
    ordered = sorted(findings, key=lambda f: (SEVERITY_RANK.get(f["severity"], 9), -f["updated_at"].timestamp()))
    return {
        "findings": [f for f in ordered if f["state"] != "resolved"],
        "recently_resolved": [f for f in ordered if f["state"] == "resolved"][:20],
        "metrics": sorted(latest, key=lambda m: m["metric_id"]),
        "known_periods": known,
        "counts": {s: sum(1 for f in findings if f["state"] != "resolved" and f["severity"] == s) for s in SEVERITY_RANK},
    }


def read_state(conn: Connection, brand_id: int) -> dict[str, Any]:
    findings = [dict(r) for r in conn.execute(
        text("""SELECT f.finding_id, f.metric_id, m.label, f.detector, f.state, f.severity, f.summary, f.opened_at, f.updated_at, f.resolved_at
                FROM monitor.findings f LEFT JOIN monitor.metrics m USING (metric_id)
                WHERE f.brand_id = :b ORDER BY f.updated_at DESC LIMIT 200"""), {"b": brand_id}).mappings()]
    latest = [dict(r) for r in conn.execute(
        text("""SELECT DISTINCT ON (e.metric_id) e.metric_id, m.label, m.unit, m.direction_bad, e.period_start, e.run_at, e.observed, e.status,
                       e.data_status, e.details->>'severity' AS severity, e.details->>'explained_by' AS explained_by,
                       t.floor_value, t.target_value, t.ceiling_value, t.seed
                FROM monitor.evaluations e
                JOIN monitor.metrics m USING (metric_id)
                LEFT JOIN monitor.target_versions t ON t.id = e.target_version_id
                WHERE e.brand_id = :b AND e.grain = 'day'
                ORDER BY e.metric_id, e.period_start DESC, e.run_at DESC"""), {"b": brand_id}).mappings()]
    known = [dict(r) for r in conn.execute(
        text("SELECT start_day, end_day, kind, reason FROM monitor.known_periods WHERE brand_id = :b ORDER BY start_day DESC"),
        {"b": brand_id}).mappings()]
    return shape_state(findings, latest, known)

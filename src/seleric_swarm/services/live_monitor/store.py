"""Postgres access for the live monitor (schema ``monitor``): state, baselines, findings, events, action requests."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text

from seleric_swarm.config.settings import get_settings
from seleric_swarm.services.live_monitor.core.context import Band, BaselineBook

DETECTOR_PREFIX = "live."


@lru_cache(maxsize=1)
def engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=3, max_overflow=2)


def get_state(conn: Connection, tenant_id: str, key: str) -> Any:
    row = conn.execute(text("SELECT value FROM monitor.live_state WHERE tenant_id = :t AND key = :k"),
                       {"t": tenant_id, "k": key}).first()
    return row[0] if row else None


def put_state(conn: Connection, tenant_id: str, key: str, value: Any) -> None:
    conn.execute(
        text("""INSERT INTO monitor.live_state(tenant_id, key, value, updated_at) VALUES (:t, :k, CAST(:v AS jsonb), NOW())
                ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"""),
        {"t": tenant_id, "k": key, "v": json.dumps(value, default=str)},
    )


def state_updated_at(conn: Connection, tenant_id: str, key: str) -> Any:
    row = conn.execute(text("SELECT updated_at FROM monitor.live_state WHERE tenant_id = :t AND key = :k"),
                       {"t": tenant_id, "k": key}).first()
    return row[0] if row else None


def write_baselines(conn: Connection, tenant_id: str, rows: list[dict[str, Any]]) -> int:
    conn.execute(text("DELETE FROM monitor.live_baselines WHERE tenant_id = :t"), {"t": tenant_id})
    if rows:
        conn.execute(
            text("""INSERT INTO monitor.live_baselines(tenant_id, level, entity_id, metric, weekday, window_days, p10, p50, p90,
                        mean, n, method, extra) VALUES (:t, :level, :entity_id, :metric, :weekday, :window_days, :p10, :p50, :p90,
                        :mean, :n, :method, CAST(:extra AS jsonb))"""),
            [{**r, "t": tenant_id, "extra": json.dumps(r.get("extra")) if r.get("extra") is not None else None} for r in rows],
        )
    return len(rows)


def load_baselines(conn: Connection, tenant_id: str, cfg: Any) -> BaselineBook:
    """Stored rows, then config overrides on top; shipped defaults per level as the last fallback."""
    return book_from_rows(conn.execute(
        text("""SELECT level, entity_id, metric, weekday, p10, p50, p90, mean, n, window_days, extra
                FROM monitor.live_baselines WHERE tenant_id = :t"""), {"t": tenant_id}
    ).mappings(), cfg)


def book_from_rows(rows: Any, cfg: Any) -> BaselineBook:
    """``baselines.compute`` rows (or the stored ones), config overrides, then the shipped defaults."""
    book = BaselineBook(min_days=cfg.baselines.min_days)
    for r in rows:
        if r["metric"].startswith("curve_"):
            curve = (r["extra"] or {}).get("curve")
            if curve:
                book.curves[r["metric"][len("curve_"):]] = [float(x) for x in curve]
            continue
        book.rows[(r["level"], r["entity_id"], r["metric"], r["weekday"])] = Band(
            r["p10"], r["p50"], r["p90"], r["mean"], int(r["n"]), f"{r['level']} {r['window_days']}d")
    for o in cfg.baselines.overrides:
        book.rows[(o.level, o.entity_id, o.metric, None)] = Band(o.p10, o.p50, o.p90, o.mean, 10_000, "override")
    for level, metrics in cfg.baselines.defaults.items():
        book.defaults[level] = {m: Band(b.p10, b.p50, b.p90, b.mean, 0, f"default ({level})") for m, b in metrics.items()}
    return book


def baselines_view(conn: Connection, tenant_id: str, level: str | None, entity_id: str | None, limit: int = 500) -> list[dict[str, Any]]:
    sql = """SELECT level, entity_id, metric, weekday, window_days, p10, p50, p90, mean, n, method, computed_at
             FROM monitor.live_baselines WHERE tenant_id = :t AND metric NOT LIKE 'curve_%'"""
    params: dict[str, Any] = {"t": tenant_id, "lim": limit}
    if level:
        sql += " AND level = :l"
        params["l"] = level
    if entity_id:
        sql += " AND entity_id = :e"
        params["e"] = entity_id
    sql += " ORDER BY level, entity_id, metric, weekday NULLS FIRST LIMIT :lim"
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def live_findings(conn: Connection, brand_id: int | None, platform: str | None = None) -> dict[str, dict[str, Any]]:
    sql = "SELECT finding_id, brand_id, detector, state, severity, summary, diagnosis FROM monitor.findings WHERE detector LIKE :p"
    params: dict[str, Any] = {"p": f"{DETECTOR_PREFIX}{platform or ''}%"}
    if brand_id is not None:
        sql += " AND brand_id = :b"
        params["b"] = brand_id
    return {r["finding_id"]: dict(r) for r in conn.execute(text(sql), params).mappings()}


def upsert_finding(conn: Connection, f: dict[str, Any], state: str) -> None:
    conn.execute(
        text("""INSERT INTO monitor.findings(finding_id, brand_id, metric_id, entity_type, entity_id, detector, state, severity,
                    confidence, impact_low, impact_high, summary, diagnosis, recommendation)
                VALUES (:finding_id, :brand_id, NULL, :entity_type, :entity_id, :detector, :state, :severity, :confidence,
                    :impact, :impact, :summary, CAST(:diagnosis AS jsonb), CAST(:recommendation AS jsonb))
                ON CONFLICT (finding_id) DO UPDATE SET state = :state, severity = :severity, confidence = :confidence,
                    impact_low = :impact, impact_high = :impact, summary = :summary, updated_at = NOW(), resolved_at = NULL,
                    diagnosis = CAST(:diagnosis AS jsonb), recommendation = CAST(:recommendation AS jsonb),
                    opened_at = CASE WHEN monitor.findings.state = 'resolved' THEN NOW() ELSE monitor.findings.opened_at END"""),
        {**f, "state": state, "diagnosis": json.dumps(f["diagnosis"], default=str),
         "recommendation": json.dumps(f["recommendation"], default=str)},
    )


def resolve_finding(conn: Connection, finding_id: str, diagnosis: dict[str, Any] | None = None) -> None:
    conn.execute(
        text("""UPDATE monitor.findings SET state = 'resolved', resolved_at = NOW(), updated_at = NOW(),
                diagnosis = COALESCE(CAST(:d AS jsonb), diagnosis) WHERE finding_id = :f"""),
        {"f": finding_id, "d": json.dumps(diagnosis, default=str) if diagnosis is not None else None},
    )


def set_diagnosis(conn: Connection, finding_id: str, diagnosis: dict[str, Any]) -> None:
    conn.execute(text("UPDATE monitor.findings SET diagnosis = CAST(:d AS jsonb) WHERE finding_id = :f"),
                 {"f": finding_id, "d": json.dumps(diagnosis, default=str)})


def add_event(conn: Connection, finding_id: str, from_state: str | None, to_state: str, note: str,
              details: dict[str, Any] | None = None) -> None:
    conn.execute(
        text("""INSERT INTO monitor.finding_events(finding_id, from_state, to_state, note, details)
                VALUES (:f, :a, :b, :n, CAST(:d AS jsonb))"""),
        {"f": finding_id, "a": from_state, "b": to_state, "n": note, "d": json.dumps(details, default=str) if details else None},
    )


def open_alerts(conn: Connection, brand_id: int, platform: str) -> list[tuple[str, str, str]]:
    """(detector, severity, entity id) of open critical / warning live findings."""
    rows = conn.execute(
        text("""SELECT detector, severity, entity_id FROM monitor.findings WHERE brand_id = :b AND detector LIKE :p
                AND state <> 'resolved' AND severity IN ('critical', 'warning')"""),
        {"b": brand_id, "p": f"{DETECTOR_PREFIX}{platform}.%"},
    ).all()
    return [(r[0], r[1], r[2]) for r in rows]


def lock_connection() -> Connection:
    """A session for the advisory lock; autocommit so it never sits idle in a transaction."""
    return engine().connect().execution_options(isolation_level="AUTOCOMMIT")


def try_lock(conn: Connection, name: str) -> bool:
    return bool(conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:n))"), {"n": name}).scalar())


def unlock(conn: Connection, name: str) -> None:
    conn.execute(text("SELECT pg_advisory_unlock(hashtext(:n))"), {"n": name})

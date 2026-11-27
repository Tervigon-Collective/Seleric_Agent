"""Live ads monitor: every 5 minutes, discover the live tree, run the due rules, reconcile findings, narrate changes.

    python -m seleric_swarm.services.live_monitor.runner --loop            # the service (keeps caches warm)
    python -m seleric_swarm.services.live_monitor.runner --once [--dry-run] [--tenant th-meta]
    python -m seleric_swarm.services.live_monitor.runner --baselines-now   # recompute baselines and guardrails

State in Postgres (schema ``monitor``): findings / finding_events (detector ``live.<platform>.<rule>``), live_state
(cadence clocks, heartbeat with the pulse, miss counters, last snapshot, guardrails, tracking reference), live_baselines.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from seleric_swarm.services.live_monitor import baselines as B
from seleric_swarm.services.live_monitor import config as C
from seleric_swarm.services.live_monitor import diagnose as D
from seleric_swarm.services.live_monitor import guardrails as G
from seleric_swarm.services.live_monitor import narrate as N
from seleric_swarm.services.live_monitor import store
from seleric_swarm.services.live_monitor.core.context import RuleContext
from seleric_swarm.services.live_monitor.core.model import (
    CADENCES,
    SEVERITY_RANK,
    WINDOWS,
    Finding,
    Snapshot,
)
from seleric_swarm.services.live_monitor.core.registry import (
    ACTIONS,
    ADAPTERS,
    RULES,
    ensure_loaded,
)
from seleric_swarm.services.live_monitor.engine import evaluate
from seleric_swarm.services.live_monitor.mcp import McpClient

log = logging.getLogger("seleric.live_monitor")
TICK_S = CADENCES["5m"]
DAILY_RULE_HOUR = 10  # yesterday's store orders and Meta's 3-day windows have settled by then
BASELINE_HOUR = 3
PUSHGATEWAY_URL = os.getenv("MONITORING_PUSHGATEWAY_URL", "http://172.17.0.1:9091").rstrip("/")


# ---------------------------------------------------------------------------------------------- pure helpers

def due_cadences(clocks: dict[str, str], now: datetime) -> set[str]:
    """5m always; 1h when an hour has passed; daily once per local day after DAILY_RULE_HOUR."""
    due = {"5m"}
    last_h = clocks.get("1h")
    if not last_h or now - datetime.fromisoformat(last_h) >= timedelta(seconds=CADENCES["1h"] - 60):
        due.add("1h")
    last_d = clocks.get("daily")
    if now.hour >= DAILY_RULE_HOUR and (not last_d or datetime.fromisoformat(last_d).astimezone(now.tzinfo).date() < now.date()):
        due.add("daily")
    return due


def baselines_due(last: str | None, now: datetime) -> bool:
    if not last:
        return True
    return now.hour >= BASELINE_HOUR and datetime.fromisoformat(last).astimezone(now.tzinfo).date() < now.date()


@dataclass
class Plan:
    """What the reconcile will write. ``changes`` are (finding_id, change) for narration."""

    upserts: list[tuple[Finding, str]] = field(default_factory=list)  # (finding, new state)
    resolves: list[str] = field(default_factory=list)
    events: list[tuple[str, str | None, str, str]] = field(default_factory=list)  # (fid, from, to, note)
    changes: list[tuple[str, str]] = field(default_factory=list)
    misses: dict[str, int] = field(default_factory=dict)


def reconcile(findings: list[Finding], existing: dict[str, dict[str, Any]], evaluated: set[str], frozen: set[str],
              misses: dict[str, int], cooldown: int, platform: str) -> Plan:
    """Pure. Open / reopen / severity change / steady update for hits; a finding of an evaluated, unfrozen rule with no
    hit resolves after ``cooldown`` further misses (so a metric hovering at the line does not flap)."""
    plan = Plan()
    seen = set()
    for f in findings:
        if f.rule_id in frozen:
            continue
        seen.add(f.finding_id)
        old = existing.get(f.finding_id)
        if old is None:
            plan.upserts.append((f, "open"))
            plan.events.append((f.finding_id, None, "open", "opened"))
            plan.changes.append((f.finding_id, "opened"))
        elif old["state"] == "resolved":
            plan.upserts.append((f, "open"))
            plan.events.append((f.finding_id, "resolved", "open", "reopened"))
            plan.changes.append((f.finding_id, "reopened"))
        else:
            plan.upserts.append((f, "ongoing"))
            if old["severity"] != f.hit.severity:
                worse = SEVERITY_RANK[f.hit.severity] < SEVERITY_RANK[old["severity"]]
                note = f"severity {old['severity']} → {f.hit.severity}"
                plan.events.append((f.finding_id, old["state"], "ongoing", note))
                plan.changes.append((f.finding_id, "escalated" if worse else "eased"))
    prefix = f"live.{platform}."
    for fid, old in existing.items():
        rule_id = str(old.get("detector") or "")[len(prefix):]
        if fid in seen or old["state"] == "resolved" or rule_id not in evaluated or rule_id in frozen:
            continue
        n = misses.get(fid, 0) + 1
        if n > cooldown:
            plan.resolves.append(fid)
            plan.events.append((fid, old["state"], "resolved", "no longer detected"))
            plan.changes.append((fid, "resolved"))
        else:
            plan.misses[fid] = n
    return plan


def finding_row(f: Finding, old_diag: dict[str, Any] | None) -> dict[str, Any]:
    e = f.entity
    actions = []
    for aid in f.hit.actions:
        a = ACTIONS.get((f.platform, aid))
        if a is not None and e.level in a.levels:
            actions.append({"id": a.id, "title": a.title, "risk": a.risk, "inputs": [i.name for i in a.inputs]})
    old_diag = old_diag or {}
    diag = {
        "live": True, "rule_id": f.rule_id, "title": f.title, "tier": f.tier, "platform": f.platform, "tenant_id": f.tenant_id,
        "playbook_ref": f.playbook_ref,
        "entity": {"level": e.level, "id": e.id, "name": e.name, "path": list(f.path), "campaign_id": e.campaign_id,
                   "adset_id": e.adset_id, "effective_status": e.effective_status},
        "evidence": f.hit.evidence, "actions": actions,
        "narration": old_diag.get("narration"), "narration_source": old_diag.get("narration_source"),
        "narrated_at": old_diag.get("narrated_at"),
        # Diagnosis fields survive steady ticks until the next open/reopen/escalate pass.
        "status": old_diag.get("status") or "pending",
        "cause": old_diag.get("cause"), "bullets": old_diag.get("bullets"), "artifacts": old_diag.get("artifacts"),
        "gates": old_diag.get("gates"), "recommended_action_id": old_diag.get("recommended_action_id"),
        "recommended_summary": old_diag.get("recommended_summary"), "diagnosed_at": old_diag.get("diagnosed_at"),
        "diagnosed_by": old_diag.get("diagnosed_by"), "diagnosis_source": old_diag.get("diagnosis_source"),
    }
    return {"finding_id": f.finding_id, "brand_id": f.brand_id, "entity_type": e.level, "entity_id": e.id,
            "detector": f.detector, "severity": f.hit.severity, "confidence": f.hit.confidence,
            "impact": round(f.hit.impact_inr, 2) if f.hit.impact_inr is not None else None, "summary": f.hit.summary,
            "diagnosis": diag, "recommendation": {"actions": actions}, "title": f.title}


def frozen_rules(snap: Snapshot) -> set[str]:
    """A missing metric window would read as 'no problem' and resolve real findings; freeze performance rules then."""
    account = snap.account()
    missing = set(WINDOWS) - set(account.metrics if account else {})
    return {rid for rid, r in RULES.items() if r.tier == "performance"} if missing else set()


def pulse(snap: Snapshot, ctx: RuleContext) -> dict[str, Any]:
    a = snap.account()
    m = a.metrics.get("today", {}) if a else {}
    now = ctx.now_local
    share = ctx.baselines.cum_share("spend", now.hour, now.minute)
    from seleric_swarm.services.live_monitor.rules.performance import daily_budget_inr

    budget = daily_budget_inr(ctx, a) if a else None
    expected = budget * share if budget and share else None
    counts = {lvl: sum(1 for e in snap.entities if e.level == lvl and snap.deliverable(e)) for lvl in ("campaign", "adset", "ad")}
    return {"spend": m.get("spend"), "purchases": m.get("purchases"), "purchase_value": m.get("purchase_value"),
            "roas": m.get("roas"), "cpa": m.get("cpa"), "cpm": m.get("cpm"), "ctr_link": m.get("ctr_link"),
            "daily_budget": budget, "expected_spend_by_now": expected,
            "pace": (m.get("spend") or 0) / expected if expected else None, "active": counts,
            "guardrails": ctx.guardrails.to_json()}


# ---------------------------------------------------------------------------------------------------- I/O

def evaluate_stored(cfg: Any, tenant: Any) -> dict[str, Any]:
    """Run every enabled rule of ``cfg`` on the last stored snapshot (no platform calls): what a config would flag."""
    ensure_loaded()
    with store.engine().connect() as conn:
        raw = store.get_state(conn, tenant.tenant_id, "snapshot")
        if not raw:
            return {"tenant_id": tenant.tenant_id, "error": "no snapshot yet; the monitor has not run"}
        snap = Snapshot.from_json(raw)
        ctx = RuleContext(snap, store.load_baselines(conn, tenant.tenant_id, cfg),
                          G.effective(cfg.guardrails, store.get_state(conn, tenant.tenant_id, "guardrails")), tenant, cfg.stats,
                          snap.fetched_at.astimezone(ZoneInfo(tenant.timezone)), store.get_state(conn, tenant.tenant_id, "tracking") or {})
    ev = evaluate(ctx, cfg, tenant, None)
    return {
        "tenant_id": tenant.tenant_id, "snapshot_at": snap.fetched_at.isoformat(),
        "counts": {s: sum(1 for f in ev.findings if f.hit.severity == s) for s in ("critical", "warning", "info")},
        "by_rule": {r: sum(1 for f in ev.findings if f.rule_id == r) for r in sorted({f.rule_id for f in ev.findings})},
        "findings": [{"rule_id": f.rule_id, "title": f.title, "severity": f.hit.severity, "path": list(f.path), "level": f.entity.level,
                      "summary": f.hit.summary} for f in ev.findings[:200]],
        "errors": ev.errors,
    }

async def check_url(url: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as h:
            r = await h.get(url, headers={"User-Agent": "SelericLiveMonitor/1.0"})
        return {"url_status": r.status_code, "final_url": str(r.url), "url_ms": round((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        return {"url_status": None, "url_error": f"{type(exc).__name__}: {exc}"[:200]}


async def refresh_baselines(adapter: Any, client: McpClient, tenant: Any, cfg: Any, now: datetime) -> dict[str, Any]:
    """Nightly: baselines, finance guardrails and the tracking reference. Each part fails on its own."""
    out: dict[str, Any] = {"errors": []}
    history: list[dict[str, Any]] = []
    try:
        history = await adapter.daily_history(tenant, cfg.baselines.window_days)
        hourly = await adapter.hourly_profile(tenant, cfg.baselines.curve_days)
        rows = B.compute(history, hourly, window_days=cfg.baselines.window_days, weekday_aware=cfg.baselines.weekday_aware,
                         curve_days=cfg.baselines.curve_days, account_id=tenant.account_id)
        with store.engine().begin() as conn:
            out["baseline_rows"] = store.write_baselines(conn, tenant.tenant_id, rows)
    except Exception as exc:
        log.exception("baselines failed for %s", tenant.tenant_id)
        out["errors"].append(f"baselines: {exc}")
    try:
        value = sum(float(r.get("purchase_value") or 0) for r in history)
        orders = sum(float(r.get("purchases") or 0) for r in history)
        finance = await client.call("metrics_query", G.finance_query(tenant.brand_id, now.date()))
        g = G.compute(finance, value / orders if orders else None, cfg.guardrails.target_roas_multiple, now.isoformat())
        if g is None:
            out["errors"].append("guardrails: finance metrics incomplete; keeping the previous values")
        else:
            with store.engine().begin() as conn:
                store.put_state(conn, tenant.tenant_id, "guardrails", g)
            out["guardrails"] = g
    except Exception as exc:
        log.exception("guardrails failed for %s", tenant.tenant_id)
        out["errors"].append(f"guardrails: {exc}")
    try:
        shop = await client.call("metrics_query", G.store_orders_query(tenant.brand_id, now.date(), cfg.baselines.window_days))
        ref = G.tracking_reference(B.pixel_by_day(history), shop)
        with store.engine().begin() as conn:
            store.put_state(conn, tenant.tenant_id, "tracking", ref)
        out["tracking_median_ratio"] = ref.get("median_ratio")
    except Exception as exc:
        log.exception("tracking reference failed for %s", tenant.tenant_id)
        out["errors"].append(f"tracking: {exc}")
    with store.engine().begin() as conn:
        store.put_state(conn, tenant.tenant_id, "baselines_meta", {"at": now.isoformat(), **out})
    return out


class Monitor:
    def __init__(self, client: McpClient, llm: Any = None, llm_model: str = "") -> None:
        ensure_loaded()
        self.client = client
        self.llm, self.llm_model = llm, llm_model
        self.adapters: dict[str, Any] = {}

    def adapter(self, platform: str) -> Any:
        if platform not in self.adapters:
            self.adapters[platform] = ADAPTERS[platform](self.client)
        return self.adapters[platform]

    async def tick(self, *, dry_run: bool = False, only_tenant: str | None = None, force_baselines: bool = False) -> list[dict[str, Any]]:
        cfg, meta = C.load()
        results = []
        for t in cfg.tenants:
            if not t.enabled or (only_tenant and t.tenant_id != only_tenant):
                continue
            try:
                results.append(await self.run_tenant(cfg, meta, t, dry_run=dry_run, force_baselines=force_baselines))
            except Exception as exc:
                log.exception("tick failed for %s", t.tenant_id)
                results.append({"tenant_id": t.tenant_id, "status": "down", "error": str(exc)})
                if not dry_run:
                    self._heartbeat_failure(t, str(exc))
        return results

    def _heartbeat_failure(self, t: Any, error: str) -> None:
        try:
            with store.engine().begin() as conn:
                hb = store.get_state(conn, t.tenant_id, "heartbeat") or {}
                hb.update(status="down", error=error[:500], failed_at=datetime.now(ZoneInfo(t.timezone)).isoformat(),
                          consecutive_failures=int(hb.get("consecutive_failures") or 0) + 1)
                store.put_state(conn, t.tenant_id, "heartbeat", hb)
        except Exception:
            log.exception("could not record the failed heartbeat")

    async def run_tenant(self, cfg: Any, meta: dict[str, Any], t: Any, *, dry_run: bool, force_baselines: bool) -> dict[str, Any]:
        started = time.perf_counter()
        now = datetime.now(ZoneInfo(t.timezone))
        adapter = self.adapter(t.platform)
        with store.engine().connect() as conn:
            clocks = store.get_state(conn, t.tenant_id, "clocks") or {}
            last_baselines = (store.get_state(conn, t.tenant_id, "baselines_meta") or {}).get("at")
        baseline_run = None
        if force_baselines or (not dry_run and baselines_due(last_baselines, now)):
            baseline_run = await refresh_baselines(adapter, self.client, t, cfg, now)

        snap = await adapter.discover(t)
        with store.engine().connect() as conn:
            book = store.load_baselines(conn, t.tenant_id, cfg)
            guard = G.effective(cfg.guardrails, store.get_state(conn, t.tenant_id, "guardrails"))
            tracking = store.get_state(conn, t.tenant_id, "tracking") or {}
            existing = store.live_findings(conn, t.brand_id, t.platform)
            misses = store.get_state(conn, t.tenant_id, "misses") or {}
        ctx = RuleContext(snap, book, guard, t, cfg.stats, now, tracking)
        due = due_cadences(clocks, now)
        ev = evaluate(ctx, cfg, t, due)
        frozen = frozen_rules(snap)

        for f in ev.findings:
            if f.rule_id == "landing_break" and f.hit.evidence.get("url") and f.finding_id not in existing:
                f.hit.evidence.update(await check_url(f.hit.evidence["url"]))

        plan = reconcile(ev.findings, existing, ev.evaluated_rules, frozen, misses, cfg.notify.cooldown_ticks, t.platform)
        by_sev = {s: sum(1 for f in ev.findings if f.hit.severity == s) for s in ("critical", "warning", "info")}
        result: dict[str, Any] = {
            "tenant_id": t.tenant_id, "at": now.isoformat(), "due": sorted(due), "entities": len(snap.entities),
            "findings": by_sev, "opened": sum(1 for _, c in plan.changes if c in ("opened", "reopened")),
            "resolved": len(plan.resolves), "partial_errors": snap.partial_errors, "rule_errors": ev.errors,
            "frozen_rules": sorted(frozen), "config_error": meta.get("error"), "baselines": baseline_run,
        }
        if dry_run:
            result["preview"] = [f"[{f.hit.severity}] {f.title} · {' › '.join(f.path) or 'Account'}: {f.hit.summary}" for f in ev.findings]
            return result

        rows = {f.finding_id: finding_row(f, (existing.get(f.finding_id) or {}).get("diagnosis")) for f, _ in plan.upserts}
        with store.engine().begin() as conn:
            for f, state in plan.upserts:
                store.upsert_finding(conn, rows[f.finding_id], state)
            for fid in plan.resolves:
                store.resolve_finding(conn, fid)
            for fid, a, b, note in plan.events:
                row = rows.get(fid) or existing.get(fid) or {}
                ent = ((row.get("diagnosis") or {}).get("entity") if isinstance(row.get("diagnosis"), dict)
                       else None) or {}
                store.add_event(conn, fid, a, b, note, {
                    "kind": "lifecycle", "actor": "live_monitor", "actor_type": "system",
                    "at": now.isoformat(), "entity_level": ent.get("level") or row.get("entity_type"),
                    "entity_id": ent.get("id") or row.get("entity_id"),
                    "rule_id": (row.get("diagnosis") or {}).get("rule_id") if isinstance(row.get("diagnosis"), dict) else None,
                    "severity": row.get("severity"), "change": note,
                })
            store.put_state(conn, t.tenant_id, "misses", plan.misses)
            clocks.update({c: now.isoformat() for c in due})
            store.put_state(conn, t.tenant_id, "clocks", clocks)
            store.put_state(conn, t.tenant_id, "snapshot", snap.to_json())
            status = "degraded" if snap.partial_errors or ev.errors or meta.get("error") else "ok"
            store.put_state(conn, t.tenant_id, "heartbeat", {
                "status": status, "at": now.isoformat(), "tick_ms": round((time.perf_counter() - started) * 1000),
                "tick_s": TICK_S, "due": sorted(due), "consecutive_failures": 0, "pulse": pulse(snap, ctx),
                "findings": by_sev, "partial_errors": snap.partial_errors[:10], "rule_errors": ev.errors[:10],
                "config_error": meta.get("error"), "config_version": meta.get("version"),
                "baselines_at": (baseline_run and now.isoformat()) or last_baselines,
            })
            alerts = store.open_alerts(conn, t.brand_id, t.platform)

        await self._diagnose_and_narrate(plan, rows, existing, guard.to_json(), cfg.notify.max_narrations_per_tick)
        quiet = cfg.notify.quiet_hours is not None and cfg.notify.quiet_hours.contains(now.time())
        push(t, status, [a for a in alerts if a[1] == "critical" or not quiet])
        result["status"] = status
        result["tick_ms"] = round((time.perf_counter() - started) * 1000)
        return result

    async def _diagnose_and_narrate(self, plan: Plan, rows: dict[str, dict[str, Any]],
                                    existing: dict[str, dict[str, Any]], guardrails: dict[str, Any],
                                    limit: int) -> None:
        """Diagnose once on open/reopen/escalate; narrate eased/resolved. Capped by max_narrations_per_tick."""
        todo = plan.changes[:limit]
        if not todo:
            return
        diagnose_changes = {"opened", "reopened", "escalated"}

        async def one(fid: str, change: str) -> tuple[str, str, dict[str, Any], dict[str, Any] | None]:
            row = rows.get(fid)
            if row is None:  # resolved: narrate from the stored finding
                old = existing.get(fid) or {}
                d = old.get("diagnosis") or {}
                row = {"finding_id": fid, "brand_id": old.get("brand_id"), "title": d.get("title") or fid,
                       "severity": old.get("severity"), "summary": old.get("summary") or "", "impact": None,
                       "confidence": old.get("confidence"), "diagnosis": d}
            if change in diagnose_changes:
                fields = await D.diagnose(self.llm, self.llm_model, row, guardrails, change=change)
                diag = {**row["diagnosis"], **fields}
                event = {
                    "kind": "diagnosis", "actor": D.ACTOR, "actor_type": "system",
                    "at": fields.get("diagnosed_at"), "status": fields.get("status"),
                    "entity_level": (diag.get("entity") or {}).get("level"),
                    "entity_id": (diag.get("entity") or {}).get("id"),
                    "recommended_action_id": fields.get("recommended_action_id"),
                    "source": fields.get("diagnosis_source"),
                }
                return fid, change, diag, event
            text, source = await N.narrate(self.llm, self.llm_model, change, row, guardrails)
            diag = {**row["diagnosis"], "narration": text, "narration_source": source, "narration_change": change,
                    "narrated_at": datetime.now().astimezone().isoformat()}
            return fid, change, diag, None

        done = await asyncio.gather(*[one(fid, c) for fid, c in todo])
        with store.engine().begin() as conn:
            for fid, change, diag, event in done:
                store.set_diagnosis(conn, fid, diag)
                if event:
                    store.add_event(conn, fid, None, "ongoing" if change == "escalated" else "open",
                                    f"diagnosed: {diag.get('cause') or ''}"[:500], event)


def _label(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def push(t: Any, status: str, alerts: list[tuple[str, str, str]]) -> None:
    """Health (POST, keeps the last success time) and open live alerts (PUT, so resolved ones disappear)."""
    base = f"{PUSHGATEWAY_URL}/metrics/job"
    health = [f"agent_live_monitor_tick_success {int(status != 'down')}", f"agent_live_monitor_degraded {int(status == 'degraded')}",
              f"agent_live_monitor_last_tick_timestamp_seconds {time.time():.0f}"]
    lines = [f'agent_live_monitor_finding_open{{detector="{_label(d)}",severity="{s}",entity="{_label(e)}"}} 1' for d, s, e in alerts]
    for method, url, body in (("POST", f"{base}/agent_live_monitor/tenant/{t.tenant_id}", health),
                              ("PUT", f"{base}/agent_live_monitor_findings/tenant/{t.tenant_id}", lines)):
        try:
            req = urllib.request.Request(url, data=("\n".join(body) + "\n").encode(), method=method)
            urllib.request.urlopen(req, timeout=10).close()
        except Exception:
            log.warning("pushgateway push failed: %s", url, exc_info=True)


def _llm() -> tuple[Any, str]:
    try:
        from seleric_swarm.config.settings import get_settings, helper_chat_model
        from seleric_swarm.llm.factory import build_llm

        s = get_settings()
        return build_llm(s), helper_chat_model(s) or s.primary_model()
    except Exception:
        log.warning("no LLM for narration; rule text will be used", exc_info=True)
        return None, ""


async def _main(a: argparse.Namespace) -> int:
    client = McpClient()
    if a.backtest is not None:
        from seleric_swarm.services.live_monitor import backtest as BT
        try:
            out = await BT.run(client, days=a.backtest, tenant_id=a.tenant)
            print(json.dumps(out, default=str), flush=True)
            return 0 if all(r.get("target_met") for r in out["reports"]) else 1
        finally:
            await client.aclose()
    llm, model = (None, "") if a.no_llm else _llm()
    mon = Monitor(client, llm, model)
    lock_conn = store.lock_connection()
    try:
        if not a.dry_run and not store.try_lock(lock_conn, "live_monitor"):
            log.error("another live monitor holds the lock; exiting")
            return 2
        while True:
            started = time.monotonic()
            results = await mon.tick(dry_run=a.dry_run, only_tenant=a.tenant, force_baselines=a.baselines_now)
            print(json.dumps(results, default=str), flush=True)
            a.baselines_now = False
            if not a.loop:
                return 1 if any(r.get("status") == "down" for r in results) else 0
            # align to the 5-minute wall clock so ticks land at :00, :05, ...
            await asyncio.sleep(max(5.0, TICK_S - (time.time() % TICK_S)) if time.monotonic() - started < TICK_S else 1.0)
    finally:
        if not a.dry_run:
            store.unlock(lock_conn, "live_monitor")
        lock_conn.close()
        await client.aclose()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--loop", action="store_true")
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--backtest", type=int, metavar="DAYS", help="Replay the last N days; print findings/day")
    p.add_argument("--tenant")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--baselines-now", action="store_true")
    p.add_argument("--no-llm", action="store_true")
    a = p.parse_args()
    raise SystemExit(asyncio.run(_main(a)))


if __name__ == "__main__":
    main()

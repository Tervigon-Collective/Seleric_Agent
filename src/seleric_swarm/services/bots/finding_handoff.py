"""B31: write a bot signal-run's analysis onto ``monitor.findings.diagnosis`` (findings are the record).

Preserves gates / evidence / entity / allow-listed actions from the live monitor; only fills cause, bullets,
recommended action, and bot provenance. Cap skips leave a finding event so the card is not left silent forever.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from seleric_swarm.services.live_monitor import diagnose as D
from seleric_swarm.services.live_monitor import store as live_store

_log = logging.getLogger("seleric.bots")
ACTOR = "bots.worker"
CAP_NOTE = "not investigated (cap)"


def finding_ids_from_run(trigger: dict[str, Any], merged: list[dict[str, Any]] | None) -> list[str]:
    """Distinct finding ids from the primary trigger detail plus any merged signal entries."""
    ids: list[str] = []
    for entry in (trigger, *(merged or [])):
        if not isinstance(entry, dict):
            continue
        fid = entry.get("finding_id")
        if fid and fid not in ids:
            ids.append(str(fid))
    return ids


def _headline_from_markdown(answer: str) -> str:
    for line in answer.splitlines():
        s = line.strip()
        if s.startswith("**") and s.endswith("**") and len(s) > 4:
            return s.strip("*").strip()[:400]
        if s.startswith("#"):
            return s.lstrip("#").strip()[:400]
    return ""


def map_proposal_to_action(actions: list[dict[str, Any]], proposals: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Match the first proposal to an allow-listed action id (pause / budget_down / budget_up / …)."""
    if not actions:
        return None, None
    for p in proposals:
        field = str(p.get("field") or "").lower()
        entity = str(p.get("entity") or "").lower()
        reason = str(p.get("reason") or "").lower()
        blob = f"{field} {entity} {reason}"
        preferred: str | None = None
        if "pause" in blob or "status" in field and ("pause" in reason or "paused" in reason):
            preferred = next((a["id"] for a in actions if str(a.get("id", "")).startswith("pause") or a.get("id") == "pause"), None)
        elif "budget" in blob and any(x in blob for x in ("lower", "down", "reduc", "cut", "decrease")):
            preferred = next((a["id"] for a in actions if "budget_down" in str(a.get("id", "")) or a.get("id") == "budget_down"), None)
        elif "budget" in blob and any(x in blob for x in ("raise", "up", "increas", "scale")):
            preferred = next((a["id"] for a in actions if "budget_up" in str(a.get("id", "")) or a.get("id") == "budget_up"), None)
        if preferred:
            return D.pick_recommended(actions, preferred)
    return D.pick_recommended(actions)


def build_patch(
    *,
    bot_id: str,
    run_id: str,
    outcome: str | None,
    why: str,
    answer: str,
    report: dict[str, Any] | None,
    status: str,
) -> dict[str, Any]:
    """Diagnosis fields to merge; does not include gates/evidence (caller keeps those)."""
    report = report or {}
    headline = str(report.get("headline") or "").strip() or _headline_from_markdown(answer) or (why[:400] if why else "")
    if not headline and outcome == "cleared":
        headline = "Alert cleared on live re-check."
    if not headline:
        headline = PENDING_FALLBACK if status != "failed" else "Bot could not finish this investigation."

    bullets: list[str] = []
    for m in (report.get("movers") or [])[:5]:
        if isinstance(m, dict):
            line = " · ".join(x for x in (m.get("entity"), m.get("figure"), m.get("note")) if x)
            if line:
                bullets.append(str(line)[:200])
    for c in (report.get("checks") or [])[:4]:
        if isinstance(c, dict) and c.get("label"):
            bullets.append(f"{c['label']}: {c.get('now') or '–'} ({c.get('read') or '–'})"[:200])
    for c in (report.get("eda_checks") or [])[:4]:
        if isinstance(c, dict) and c.get("label") and len(bullets) < 6:
            bullets.append(f"{c['label']}: {c.get('now')} ({c.get('read')})"[:200])
    if not bullets and why:
        bullets = [why[:200]]

    ready = status in ("completed", "partial") and bool(answer.strip())
    diag_status = "ready" if ready else "failed"
    source = f"bot:{bot_id}"
    now = datetime.now().astimezone().isoformat()
    return {
        "status": diag_status,
        "cause": headline[:400],
        "bullets": bullets[:6],
        "narration": headline[:400],
        "narration_source": source,
        "narrated_at": now,
        "diagnosed_at": now,
        "diagnosed_by": ACTOR,
        "diagnosis_source": source,
        "bot_run_id": run_id,
        "bot_id": bot_id,
        "bot_outcome": outcome,
        "proposals": list(report.get("proposals") or []),
    }


PENDING_FALLBACK = D.PENDING_CAUSE


def apply_bot_diagnosis(
    conn: Connection,
    finding_id: str,
    *,
    bot_id: str,
    run_id: str,
    outcome: str | None,
    why: str,
    answer: str,
    report: dict[str, Any] | None,
    run_status: str,
) -> None:
    """Merge the bot's analysis into the finding; preserve gates/evidence/entity/actions."""
    row = conn.execute(
        text("SELECT diagnosis, state FROM monitor.findings WHERE finding_id = :f"),
        {"f": finding_id},
    ).mappings().first()
    if row is None:
        _log.warning("finding_handoff_missing finding=%s run=%s", finding_id, run_id)
        return
    existing = dict(row["diagnosis"] or {})
    actions = list(existing.get("actions") or [])
    patch = build_patch(bot_id=bot_id, run_id=run_id, outcome=outcome, why=why, answer=answer,
                        report=report, status=run_status)
    proposals = [p for p in (report or {}).get("proposals") or [] if isinstance(p, dict)]
    if proposals and actions and patch["status"] == "ready":
        rid, rtitle = map_proposal_to_action(actions, proposals)
        if rid:
            patch["recommended_action_id"] = rid
            patch["recommended_summary"] = rtitle
    else:
        # Keep the rule default already on the finding
        patch.setdefault("recommended_action_id", existing.get("recommended_action_id"))
        patch.setdefault("recommended_summary", existing.get("recommended_summary"))

    merged = {**existing, **patch}
    live_store.set_diagnosis(conn, finding_id, merged)
    live_store.add_event(
        conn, finding_id, None, row["state"] or "open",
        f"bot diagnosis: {patch['cause']}"[:500],
        {
            "kind": "diagnosis",
            "actor": ACTOR,
            "actor_type": "system",
            "at": patch["diagnosed_at"],
            "status": patch["status"],
            "recommended_action_id": patch.get("recommended_action_id"),
            "source": patch["diagnosis_source"],
            "bot_run_id": run_id,
            "bot_outcome": outcome,
        },
    )


def note_not_investigated(conn: Connection, finding_id: str, *, why: str = CAP_NOTE, bot_id: str | None = None) -> None:
    """Mark a finding that a signal trigger saw but the bot did not run (daily cap)."""
    row = conn.execute(
        text("SELECT diagnosis, state FROM monitor.findings WHERE finding_id = :f"),
        {"f": finding_id},
    ).mappings().first()
    if row is None:
        return
    existing = dict(row["diagnosis"] or {})
    now = datetime.now().astimezone().isoformat()
    patch = {
        **existing,
        "status": existing.get("status") or "pending_bot",
        "cause": existing.get("cause") if existing.get("diagnosis_source", "").startswith("bot:") else why,
        "narration": existing.get("narration") if existing.get("diagnosis_source", "").startswith("bot:") else why,
        "bot_skip_reason": why,
        "bot_skip_at": now,
        "bot_skip_bot_id": bot_id,
    }
    if not str(existing.get("diagnosis_source") or "").startswith("bot:"):
        patch["diagnosis_source"] = "awaiting_bot"
        patch["narration_source"] = "awaiting_bot"
    live_store.set_diagnosis(conn, finding_id, patch)
    live_store.add_event(
        conn, finding_id, None, row["state"] or "open", why[:500],
        {"kind": "diagnosis", "actor": ACTOR, "actor_type": "system", "at": now, "status": "skipped_cap",
         "source": "awaiting_bot", "bot_id": bot_id},
    )


def write_back(
    conn: Connection,
    *,
    bot_id: str,
    run_id: str,
    trigger_kind: str,
    trigger: dict[str, Any],
    merged: list[dict[str, Any]] | None,
    outcome: str | None,
    why: str,
    answer: str,
    report: dict[str, Any] | None,
    run_status: str,
) -> list[str]:
    """Write bot analysis onto every finding linked to this run. No-op for non-signal runs."""
    if trigger_kind != "signal":
        return []
    # Merged may include cron/watch entries without finding_id — those are skipped.
    ids = finding_ids_from_run(trigger, merged)
    for fid in ids:
        apply_bot_diagnosis(conn, fid, bot_id=bot_id, run_id=run_id, outcome=outcome, why=why,
                            answer=answer, report=report, run_status=run_status)
    return ids

"""Where a finished bot run goes besides the Bots page (D-244 B7, B19, B20): the Notification Center feed and email.

B20 rule, evaluated once per run: a scheduled (cron) run always reports (daily summary); any automatic run that ends
``action_needed`` reports; ``unverified`` reports only when the bot's previous automatic run was unverified too
(two in a row); chat and manual runs never do — the person who asked is already looking at the answer.

A run that reports gets one ``delivery`` activity row (``tool = notification_center``); the Notification Center reads
those rows, so the rule lives here only. Email goes to the bot's own recipient list (B19). The operator chose those
recipients when configuring the bot, which is the approval for these summaries; the send itself goes through the
existing MailService (one sender wins, status recorded).
"""

from __future__ import annotations

import logging
from typing import Any

from seleric_swarm.mail.draft import AdHoc, render_ad_hoc
from seleric_swarm.mail.recipients import Clarify, resolve
from seleric_swarm.mail.send import Draft, SendStatus
from seleric_swarm.mail.session import get_mail_runtime

_log = logging.getLogger("seleric.bots.delivery")

NEVER = ("chat", "manual")
OUTCOME_WORDS = {"action_needed": "action needed", "no_action": "no action", "cleared": "cleared", "unverified": "could not check"}


def why_notify(trigger_kind: str, outcome: str | None, previous_outcome: str | None) -> str | None:
    """Why this run is reported, or None when it stays on the Bots page only."""
    if trigger_kind in NEVER:
        return None
    if outcome == "action_needed":
        return "action needed"
    if outcome == "unverified" and previous_outcome == "unverified":
        return "could not check, twice in a row"
    if trigger_kind == "cron":
        return "scheduled summary"
    return None


def _body(answer: str) -> str:
    """The answer without its machine-readable OUTCOME line (the subject carries the verdict)."""
    lines = answer.strip().splitlines()
    while lines and lines[-1].strip().strip("*").strip().upper().startswith("OUTCOME:"):
        lines.pop()
    return "\n".join(lines).strip()


def subject(bot: dict[str, Any], outcome: str | None, why: str) -> str:
    verdict = OUTCOME_WORDS.get(outcome or "", "finished")
    return f"[Seleric bot] {bot['name']}: {verdict}" + (f" — {why[:120]}" if why else "")


async def email(bot: dict[str, Any], run: dict[str, Any], outcome: str | None, why: str, answer: str) -> dict[str, Any] | None:
    """Send the run to the bot's recipients; the activity row describing what happened (None: nobody to send to)."""
    refs = list((bot.get("notify") or {}).get("email") or [])
    if not refs:
        return None
    mail = get_mail_runtime()
    if mail is None:
        return {"tool": "email", "outcome": "failed", "summary": "Email is not set up on this server; nothing sent."}
    to: list[str] = []
    for ref in refs:
        found = resolve(ref, mail.contacts)
        if isinstance(found, Clarify):  # a contact removed since the bot was saved: skip it, say so
            _log.warning("bots_email_recipient_unresolved bot=%s", bot["bot_id"])
            continue
        to += [a for a in found.addresses if a not in to]
    if not to:
        return {"tool": "email", "outcome": "failed", "summary": "None of the bot's email recipients resolve any more."}
    footer = f"Started by: {run['trigger_kind']} — {run['reason'][:300]}\n\nSent by the Seleric bot \"{bot['name']}\" (read-only)."
    rendered = render_ad_hoc(AdHoc(subject=subject(bot, outcome, why), body=f"{_body(answer) or why}\n\n{footer}"))
    draft = Draft(sender=mail.sender, to=tuple(to), subject=rendered.subject, html=rendered.html, text=rendered.text)
    try:
        rec = mail.service.create(draft, meta={"bot_id": bot["bot_id"], "bot_run_id": run["run_id"]}, run_id=run["run_id"])
        mail.service.approve(rec.id, rec.version)
        rec = await mail.service.send(rec.id)
    except Exception as exc:
        return {"tool": "email", "target": ", ".join(to), "outcome": "failed", "summary": f"Email not sent: {type(exc).__name__}"}
    sent = rec.status is SendStatus.SUBMITTED
    return {"tool": "email", "target": ", ".join(to), "outcome": "completed" if sent else "failed",
            "summary": f"Emailed {len(to)} recipient(s)" if sent else f"Email {rec.status.value}: {rec.error[:200]}",
            "detail": {"email_id": rec.id}}


async def deliver(bot: dict[str, Any], run: dict[str, Any], outcome: str | None, why: str, answer: str,
                  previous_outcome: str | None) -> list[dict[str, Any]]:
    """Activity rows (kind ``delivery``) for this run: none when the B20 rule keeps it on the Bots page."""
    reason = why_notify(run["trigger_kind"], outcome, previous_outcome)
    if reason is None:
        return []
    rows = [{"tool": "notification_center", "outcome": "completed", "decision": outcome,
             "summary": f"{subject(bot, outcome, why)} ({reason})", "detail": {"reason": reason}}]
    sent = await email(bot, run, outcome, why, answer)
    return rows + ([sent] if sent else [])

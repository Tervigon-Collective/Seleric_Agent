"""Email tools: draft a MoM or an ad hoc email from the session, show it for approval, send only what was approved.

Drafting never sends. The preview is an APPROVAL part (``action: send_email``) the UI renders as a card with the
recipients, subject, body and attachments. ``send_email`` is for the user's spoken or typed approval; it refuses a
version the user has not seen yet (one produced by this same run), a stale version, someone else's draft, and a
cancelled run. The card's Approve button goes through ``POST /v1/emails/{id}/approve`` and the same service.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.grounding import grounding, problems
from seleric_swarm.agent.mcp_toolset import TurnState
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.mail.draft import (
    ActionItem,
    AdHoc,
    EmailChart,
    EmailTable,
    MoM,
    RenderedEmail,
    render_ad_hoc,
    render_mom,
)
from seleric_swarm.mail.evidence import charts_for, datasets_for, tables_for
from seleric_swarm.mail.excel import build_workbook
from seleric_swarm.mail.recipients import Clarify, resolve
from seleric_swarm.mail.send import (
    Aborted,
    Attachment,
    Draft,
    NotApproved,
    Record,
    SendStatus,
    StaleApproval,
)
from seleric_swarm.mail.session import MailRuntime, get_mail_runtime

MAX_RECIPIENTS = 20
SIGN_OFF = "Seleric Reporting Assistant"
_TABLE_REF = re.compile(r"\[\[(?:table|chart):([^\]\s]+)\]\]")
_BRACKET = re.compile(r"\[[^\[\]\n]{2,40}\]")
_NOT_SET_UP = "Email is not set up on this server (no sender account), so nothing can be drafted or sent."
_GONE = "No such draft for you (it may have expired). Draft the email again."


class MomAction(BaseModel):
    task: str
    owner: str | None = None
    deadline: str | None = None


def clean_subject(subject: str) -> str:
    """One line: a line break in a subject is a header injection, not a subject."""
    return " ".join(str(subject).split())


def unfilled(text: str, table_ids: list[str]) -> str | None:
    """A sentence about placeholders left in the email (a table that was not requested, or "[Your name]"), else None."""
    unknown = [r for r in _TABLE_REF.findall(text) if r not in table_ids]
    if unknown:
        return (f"The body points at table/chart(s) {unknown} that are not in `tables`/`charts`. Add them there or remove "
                "the placeholder.")
    left = _BRACKET.findall(_TABLE_REF.sub("", text))
    if left:
        return (f"The email still has unfilled placeholder(s) {left}. Write the real text; if you do not know the sender's "
                f"name, sign off as \"{SIGN_OFF}\".")
    return None


def _fail(summary: str) -> ToolResult:
    return ToolResult(success=False, retryable=True, error_code="EMAIL", summary=summary)


def _owned(rec: Record, deps: SelericDeps) -> bool:
    return rec.meta.get("user_id") == deps.principal.user_id and rec.meta.get("workspace_id") == deps.principal.workspace_id


def _addresses(refs: list[str] | None, runtime: MailRuntime) -> tuple[list[str], list[str]]:
    """(addresses, questions for the user). One unresolved name stops the whole draft."""
    found: list[str] = []
    questions: list[str] = []
    for ref in refs or []:
        out = resolve(ref, runtime.contacts)
        if isinstance(out, Clarify):
            options = f" Options: {'; '.join(out.candidates)}." if out.candidates else ""
            questions.append(f"{out.reason}{options}")
        else:
            found += out.addresses
    return list(dict.fromkeys(found)), questions


def _card(rec: Record, runtime: MailRuntime, fmt: str, text: str, partial: dict[str, str]) -> dict[str, Any]:
    d = rec.draft
    return {
        "approval_id": rec.id, "action": "send_email", "format": fmt, "version": rec.version, "status": rec.status.value,
        "summary": f"Email to {', '.join(d.to)}: {d.subject}", "required_role": "owner", "dry_run": False,
        "sender": d.sender, "to": list(d.to), "cc": list(d.cc), "subject": d.subject, "body": text,  # markdown, shown by the card
        "attachments": [{"name": a.name, "size_bytes": len(a.content), "partial": a.name in partial,
                         "note": partial.get(a.name, "")} for a in d.attachments],
    }


def build_email_tools(state: TurnState, *, is_cancelled: Callable[[], bool], figures: Callable[[], list[Any]]) -> list[Any]:
    def unbacked_figures(text: str) -> str | None:
        issues = problems(grounding(text, figures()))
        return ("; ".join(issues)) if issues else None

    def attachments_for(ctx: RunContext[SelericDeps], query_ids: list[str] | None, name: str
                        ) -> tuple[tuple[Attachment, ...], dict[str, str], str | None]:
        if not query_ids:
            return (), {}, None
        datasets, missing = datasets_for(ctx.deps, query_ids, by_query=state.by_query)
        if missing:
            return (), {}, (f"No stored rows for query id(s) {missing}: the attachment would be missing data. Nothing was "
                            f"drafted. Only these have rows: {[q for q in query_ids if q not in missing]}. Draft again with just "
                            "those and tell the user which results were left out; do not stop to ask.")
        file = f"{re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-')[:40] or 'seleric-data'}-{ctx.deps.as_of.date().isoformat()}.xlsx"
        partial = [d for d in datasets if d.partial]
        notes = {file: "PARTIAL: " + "; ".join(f"{d.name} has {len(d.rows)} of {d.total_rows} rows" for d in partial)} if partial else {}
        return (Attachment(file, build_workbook(datasets)),), notes, None

    def publish(rec: Record, runtime: MailRuntime, fmt: str, rendered: RenderedEmail, partial: dict[str, str]) -> ToolResult:
        state.parts[:] = [p for p in state.parts if (p.get("content") or {}).get("approval_id") != rec.id]
        state.parts.append({"type": "APPROVAL", "content": _card(rec, runtime, fmt, rendered.markdown, partial)})
        d = rec.draft
        files = ", ".join(a.name for a in d.attachments) or "none"
        warn = f" WARNING: the attachment is PARTIAL ({'; '.join(partial.values())}); say so to the user." if partial else ""
        return ToolResult(success=True, summary=(
            f"Draft ready; the user sees a preview card. draft_id={rec.id} version={rec.version}. From {d.sender}; to "
            f"{', '.join(d.to)}{'; cc ' + ', '.join(d.cc) if d.cc else ''}; subject \"{d.subject}\"; attachments: {files}.{warn} "
            "Do not send. Tell the user in one or two sentences who it goes to (the full addresses) and what is attached, "
            "and ask them to approve with the card or by saying confirm, send, or yes. Call send_email "
            "only after the user approves this exact version in a LATER message."))

    def tables_from(ctx: RunContext[SelericDeps], ids: list[str] | None, attached: bool
                    ) -> tuple[dict[str, list[EmailTable]], str | None]:
        if not ids:
            return {}, None
        found, missing = tables_for(ctx.deps, ids, by_query=state.by_query, attached=attached)
        if missing:
            return {}, (f"No stored rows for query id(s) {missing}: the table would be missing data. Nothing was drafted. "
                        f"Only these have rows: {[q for q in ids if q not in missing]}. Draft again with just those and tell "
                        "the user which results were left out; do not stop to ask.")
        return found, None

    def charts_from(ctx: RunContext[SelericDeps], ids: list[str] | None
                    ) -> tuple[dict[str, list[EmailChart]], tuple[Attachment, ...], str | None]:
        if not ids:
            return {}, (), None
        found, missing = charts_for(ctx.deps, ids, by_query=state.by_query)
        flat = [q for q in ids if q in found and not found[q]]
        if missing or flat:
            return {}, (), (f"Cannot chart query id(s) {missing + flat}: " + ("no stored rows" if missing else "") +
                            ("; " if missing and flat else "") + ("not a single time series (a chart needs one row per date, "
                            "no breakdown)" if flat else "") + f". Nothing was drafted. Chartable: "
                            f"{[q for q in ids if found.get(q)]}. Draft again with just those, or use a table, and tell the user.")
        return ({q: [c for c, _ in pairs] for q, pairs in found.items()},
                tuple(a for pairs in found.values() for _, a in pairs), None)

    def start(ctx: RunContext[SelericDeps], to: list[str], cc: list[str] | None, text: str, table_ids: list[str] | None = None
              ) -> tuple[MailRuntime | None, list[str], list[str], ToolResult | None]:
        runtime = get_mail_runtime()
        if runtime is None:
            return None, [], [], _fail(_NOT_SET_UP)
        if (loose := unfilled(text, table_ids or [])) is not None:
            return runtime, [], [], _fail(f"{loose} Nothing was drafted.")
        addr, ask = _addresses(to, runtime)
        cc_addr, cc_ask = _addresses(cc, runtime)
        if not addr and not ask:
            ask = ["No recipient was given."]
        if ask or cc_ask:
            return runtime, [], [], _fail("Ask the user: " + " ".join([*ask, *cc_ask]) + " Nothing was drafted.")
        if len(addr) + len(cc_addr) > MAX_RECIPIENTS:
            return runtime, [], [], _fail(f"Too many recipients (limit {MAX_RECIPIENTS}).")
        if (bad := unbacked_figures(text)) is not None:
            return runtime, [], [], _fail(f"These figures are not in this session's results: {bad}. Use the exact figures "
                                          "from the results (or remove them) and draft again. Nothing was drafted.")
        return runtime, addr, cc_addr, None

    def save(ctx: RunContext[SelericDeps], runtime: MailRuntime, draft_id: str, draft: Draft, keep_files: bool,
             revise_previous: bool = False) -> tuple[Record | None, ToolResult | None]:
        deps = ctx.deps
        if revise_previous and not draft_id:
            latest = runtime.service.latest_for_thread(deps.thread_id)
            if latest is None or not _owned(latest, deps):
                return None, _fail("There is no draft in this conversation to revise. Draft the email first.")
            draft_id = latest.id
        if draft_id:
            try:
                rec = runtime.service.get(draft_id)
            except KeyError:
                return None, _fail(_GONE)
            if not _owned(rec, deps):
                return None, _fail(_GONE)
            if rec.status not in (SendStatus.DRAFT, SendStatus.FAILED):
                return None, _fail(f"That email is already {rec.status.value}. Draft a new one (without draft_id).")
            changes: dict[str, Any] = {"to": draft.to, "cc": draft.cc, "subject": draft.subject, "html": draft.html, "text": draft.text}
            inline = tuple(a for a in draft.attachments if a.cid)  # the new html's images replace the old ones
            if not keep_files:
                changes["attachments"] = draft.attachments
            else:
                changes["attachments"] = (*(a for a in rec.draft.attachments if not a.cid), *inline)
            return runtime.service.revise(draft_id, run_id=deps.run_id, **changes), None
        meta = {"user_id": deps.principal.user_id, "workspace_id": deps.principal.workspace_id, "thread_id": deps.thread_id}
        return runtime.service.create(draft, meta=meta, run_id=deps.run_id), None

    async def draft_mom_email(
        ctx: RunContext[SelericDeps],
        to: list[str],
        topic: str,
        summary: str = "",
        discussion_points: list[str] | None = None,
        decisions: list[str] | None = None,
        action_items: list[MomAction] | None = None,
        open_items: list[str] | None = None,
        cc: list[str] | None = None,
        attach_query_ids: list[str] | None = None,
        tables: list[str] | None = None,
        charts: list[str] | None = None,
        draft_id: str = "",
        revise_previous: bool = False,
    ) -> ToolResult:
        """Draft a MoM (minutes of meeting) email of this session and show it to the user for approval. It never sends.

        Use when the user asks to send/share the discussion or its findings as a MoM or minutes. ``to``/``cc`` are
        names, groups or full addresses. Fill only what the session supports: summary, discussion_points (findings with
        their exact figures), decisions (ONLY what the user explicitly agreed: a suggestion is not a decision),
        action_items (owner/deadline only if stated), open_items. Leave a section empty rather than invent; never invent
        people, owners or dates. ``attach_query_ids`` are the query ids of fetched results to attach as an Excel file
        (only when asked or clearly useful). ``tables`` are query ids whose stored rows go into the email as data
        tables under "Data" (the tool builds them from the results; never type table rows yourself). ``charts`` are query ids
        of time-series results (one row per date, no breakdown) drawn as inline trend charts under "Data". ``revise_previous=true``
        changes the draft the user was just shown instead of creating a new one (use it when they ask for a change).
        If it fails with a question, ask the user that question."""
        items = [ActionItem(a.task, a.owner, a.deadline) for a in map(MomAction.model_validate, action_items or [])]
        mom = MoM(clean_subject(topic), ctx.deps.as_of.date().isoformat(), summary, list(discussion_points or []),
                  list(decisions or []), items, list(open_items or []))
        text = "\n".join([mom.summary, *mom.discussion_points, *mom.decisions, *(a.line() for a in items), *mom.open_items])
        runtime, addr, cc_addr, failed = start(ctx, to, cc, text, [*(tables or []), *(charts or [])])
        if failed or runtime is None:
            return failed or _fail(_NOT_SET_UP)
        files, partial, err = attachments_for(ctx, attach_query_ids, f"MoM {mom.topic}")
        if err:
            return _fail(err)
        found, err = tables_from(ctx, tables, bool(files))
        if err:
            return _fail(err)
        shown, images, err = charts_from(ctx, charts)
        if err:
            return _fail(err)
        if files or found or shown:
            mom = MoM(**{**mom.__dict__, "attachments": [a.name for a in files], "tables": [t for ts in found.values() for t in ts],
                         "charts": [c for cs in shown.values() for c in cs]})
        files = (*files, *images)
        rendered = render_mom(mom)
        draft = Draft(runtime.sender, tuple(addr), clean_subject(rendered.subject), rendered.html, rendered.text, tuple(cc_addr), files)
        rec, failed = save(ctx, runtime, draft_id, draft, keep_files=not attach_query_ids, revise_previous=revise_previous)
        return failed or publish(rec, runtime, "mom", rendered, partial)  # type: ignore[arg-type]

    async def draft_ad_hoc_email(
        ctx: RunContext[SelericDeps],
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        attach_query_ids: list[str] | None = None,
        tables: list[str] | None = None,
        charts: list[str] | None = None,
        draft_id: str = "",
        revise_previous: bool = False,
    ) -> ToolResult:
        """Draft an ad hoc email to a person or team and show it to the user for approval. It never sends.

        Use when the user asks to email/send something to someone and it is not a MoM ("email Ajay about the checkout
        issue", "send this to the marketing team"). Write ``subject`` and ``body`` as the user asked: their tone,
        language and length. For a report or update, lay it out as a reporting assistant would: a subject that carries the
        finding ("Net sales fell 10.8% week over week"), a greeting, a one-line headline with the key figure in **bold**,
        then short "## What happened", "## Why it matters" and "## Recommended next steps" sections (use "- " bullets;
        skip a section you have nothing for), and sign off. Include only what this recipient needs, quote figures exactly
        as the results gave them, and separate what was measured from what is only a possible explanation.
        Data goes in tables: pass the query ids of the fetched results in ``tables`` and put ``[[table:QUERY_ID]]`` on a
        line of its own where each table belongs; the tool builds the table from the stored rows (never type table rows
        or a list of figures into the text; the prose gives the takeaway, the table gives the data). When the user asks for a
        graph/chart/plot in the email, pass the query ids of the time-series results in ``charts`` and put ``[[chart:QUERY_ID]]``
        on a line of its own where each belongs; the tool draws it from the stored rows and embeds it in the email body
        (an Excel attachment has no charts, so never say it does). ``to``/``cc`` are
        names, groups or full addresses. ``attach_query_ids`` attach fetched results as an Excel file (when asked, or
        when the email says data is attached). Never leave placeholders like [Your name]: sign off with the user's name
        if they gave it, else "Seleric Reporting Assistant". ``revise_previous=true`` changes the draft the user was just shown (when they
        ask for a change). If it fails with a question, ask the user."""
        runtime, addr, cc_addr, failed = start(ctx, to, cc, f"{subject}\n{body}", [*(tables or []), *(charts or [])])
        if failed or runtime is None:
            return failed or _fail(_NOT_SET_UP)
        files, partial, err = attachments_for(ctx, attach_query_ids, subject)
        if err:
            return _fail(err)
        found, err = tables_from(ctx, tables, bool(files))
        if err:
            return _fail(err)
        shown, images, err = charts_from(ctx, charts)
        if err:
            return _fail(err)
        files = (*files, *images)
        rendered = render_ad_hoc(AdHoc(clean_subject(subject), body, found, shown))
        draft = Draft(runtime.sender, tuple(addr), rendered.subject, rendered.html, rendered.text, tuple(cc_addr), files)
        rec, failed = save(ctx, runtime, draft_id, draft, keep_files=not attach_query_ids, revise_previous=revise_previous)
        return failed or publish(rec, runtime, "ad_hoc", rendered, partial)  # type: ignore[arg-type]

    async def send_email(ctx: RunContext[SelericDeps], draft_id: str = "", version: int | None = None) -> ToolResult:
        """Send the drafted email AFTER the user has approved it. Call it, with no arguments, only when the user's latest
        message approves the preview they were just shown ("yes", "confirm", "send", "yes, send it", "approved",
        "go ahead"): never to finish a draft you just made, never on your own judgement, and never for a draft-only
        request. It sends the latest draft of this conversation. If they ask for a change instead, call the draft tool
        again with revise_previous=true and ask for approval of the new version. Report the result exactly: "submitted"
        means the mail system accepted it, not that it was delivered."""
        deps = ctx.deps
        runtime = get_mail_runtime()
        if runtime is None:
            return _fail(_NOT_SET_UP)
        try:
            rec = runtime.service.get(draft_id) if draft_id else runtime.service.latest_for_thread(deps.thread_id)
        except KeyError:
            rec = None
        if rec is None:
            return _fail("There is no draft email waiting for approval in this conversation. Draft it first." if not draft_id else _GONE)
        if not _owned(rec, deps):
            return _fail(_GONE)
        draft_id, version = rec.id, version if version is not None else rec.version
        if rec.status is SendStatus.SUBMITTED:
            return ToolResult(success=True, summary="This email was already submitted; nothing more was sent. "
                              "Submitted is not proof of delivery.")
        if version != rec.version:
            return _fail(f"The current version is {rec.version}, not {version}. Show the current preview and ask the user to approve it.")
        if rec.run_id == deps.run_id:
            return _fail("The user has not seen this version yet. Ask them to approve the preview (card or by saying so), "
                         "then send in a later message.")
        try:
            runtime.service.approve(draft_id, version)
            done = await runtime.service.send(draft_id, should_abort=is_cancelled)
        except Aborted:
            return _fail("The run was cancelled, so the email was not sent. The user can approve again.")
        except (NotApproved, StaleApproval) as exc:
            return _fail(str(exc))
        to = ", ".join(done.draft.to)
        if done.status is SendStatus.SUBMITTED:
            return ToolResult(success=True, summary=f"Submitted to the mail system for {to}. That means it was accepted for "
                              "sending, not that it was delivered: say \"sent\" or \"submitted\", never \"delivered\".")
        if done.status is SendStatus.FAILED:
            return _fail(f"The email failed to send and nothing went out: {done.error}. Tell the user; they can approve again to retry.")
        if done.status is SendStatus.UNKNOWN:
            return _fail(f"The outcome is unknown: {done.error}. It may or may not have gone out. Do not retry; tell the user "
                         "to check with the recipient before sending again.")
        return ToolResult(success=True, summary="Another request is already sending this email; do not send it again.")

    return [draft_mom_email, draft_ad_hoc_email, send_email]

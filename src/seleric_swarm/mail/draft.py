"""The two email formats, rendered as styled HTML, a plain-text fallback and the markdown the preview card shows.

A MoM records the discussion in fixed sections; an ad hoc email is the message the user asked for, written as given.
Nothing here invents content: an empty section is dropped, and an owner or deadline appears only when it was given.

The body is a few blocks: ``## heading``, ``- bullets``, paragraphs (``**bold**`` allowed) and data tables. A table is
placed where the body has ``[[table:QUERY_ID]]`` (any requested table without one goes at the end). Table rows come
from stored results, never from the model, and every table renders as a real HTML table with right-aligned figures.
A chart works the same with ``[[chart:QUERY_ID]]``; it renders as an inline image (``cid:``) the sender attaches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from typing import Any

_FONT = "-apple-system,'Segoe UI',Roboto,Arial,sans-serif"
_CELL = "padding:6px 10px;border:1px solid #e5e7eb;"
_PLACEHOLDER = re.compile(r"\[\[(table|chart):([^\]\s]+)\]\]")


@dataclass(frozen=True)
class EmailTable:
    title: str
    subtitle: str
    headers: list[str]
    rows: list[list[str]]
    align: list[str]  # "l" or "r" per column
    note: str = ""


@dataclass(frozen=True)
class EmailChart:
    title: str
    cid: str  # the Content-ID of the inline PNG attachment


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str
    markdown: str = ""


@dataclass(frozen=True)
class ActionItem:
    task: str
    owner: str | None = None
    deadline: str | None = None

    def line(self) -> str:
        given = [f"{k}: {v}" for k, v in (("owner", self.owner), ("due", self.deadline)) if v]
        return f"{self.task} ({', '.join(given)})" if given else self.task


@dataclass(frozen=True)
class MoM:
    topic: str
    date: str
    summary: str = ""
    discussion_points: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    open_items: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    tables: list[EmailTable] = field(default_factory=list)
    charts: list[EmailChart] = field(default_factory=list)


@dataclass(frozen=True)
class AdHoc:
    subject: str
    body: str  # blank-line separated blocks: "## heading", "- " bullets, paragraphs, [[table:QUERY_ID]]
    tables: dict[str, list[EmailTable]] = field(default_factory=dict)
    charts: dict[str, list[EmailChart]] = field(default_factory=dict)


Block = tuple[str, Any]


def _blocks(body: str, tables: dict[str, list[EmailTable]], charts: dict[str, list[EmailChart]]) -> list[Block]:
    blocks: list[Block] = []
    found: dict[str, dict[str, list[Any]]] = {"table": tables, "chart": charts}
    used: set[tuple[str, str]] = set()
    for chunk in (c.strip() for c in re.split(r"\n\s*\n", body.strip())):
        if not chunk:
            continue
        mark = _PLACEHOLDER.fullmatch(chunk)
        if mark and mark[2] in found[mark[1]]:
            blocks += [(mark[1], t) for t in found[mark[1]][mark[2]]]
            used.add((mark[1], mark[2]))
            continue
        # Inline [[table:id]] in prose (bullets etc.): drop the token so it never
        # shows in the preview; the table still lands via the unused-append below.
        if _PLACEHOLDER.search(chunk):
            chunk = _PLACEHOLDER.sub("", chunk)
            chunk = re.sub(r"\(\s*\)", "", chunk)
            chunk = re.sub(r"[ \t]{2,}", " ", chunk)
            chunk = re.sub(r" +([.,;:])", r"\1", chunk).strip()
            if not chunk:
                continue
        lines = chunk.splitlines()
        if lines[0].startswith("## "):
            blocks.append(("h2", lines[0][3:].strip()))
            lines = lines[1:]
        if lines and all(ln.startswith("- ") for ln in lines):
            blocks.append(("ul", [ln[2:].strip() for ln in lines]))
        elif lines:
            blocks.append(("p", "\n".join(lines)))
    blocks += [(kind, t) for kind, group in found.items() for key, ts in group.items() if (kind, key) not in used for t in ts]
    return blocks


def _inline(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(text))


def _table_html(t: EmailTable) -> str:
    def cell(tag: str, value: str, side: str, extra: str = "") -> str:
        align = "right" if side == "r" else "left"
        return f'<{tag} style="{_CELL}text-align:{align};{extra}">{escape(value)}</{tag}>'

    head = "".join(cell("th", h, a, "background:#f3f4f6;font-weight:600;") for h, a in zip(t.headers, t.align, strict=True))
    body = "".join(
        "<tr>" + "".join(cell("td", v, a, "background:#fafafa;" if i % 2 else "") for v, a in zip(row, t.align, strict=True)) + "</tr>"
        for i, row in enumerate(t.rows))
    caption = f'<p style="margin:16px 0 4px"><strong>{escape(t.title)}</strong>' + (
        f' <span style="color:#6b7280">· {escape(t.subtitle)}</span>' if t.subtitle else "") + "</p>"
    note = f'<p style="margin:4px 0;color:#6b7280;font-size:12px">{escape(t.note)}</p>' if t.note else ""
    return (f'{caption}<table style="border-collapse:collapse;width:auto;max-width:100%;font-size:13px;margin:4px 0"><tr>{head}</tr>{body}</table>{note}')


def _chart_html(c: EmailChart) -> str:
    return (f'<p style="margin:16px 0 4px"><img src="cid:{escape(c.cid)}" alt="{escape(c.title)}" width="640" '
            'style="max-width:100%;height:auto;border:1px solid #e5e7eb"></p>')


def _to_html(blocks: list[Block]) -> str:
    out: list[str] = []
    for kind, value in blocks:
        if kind == "h2":
            out.append(f'<h2 style="font-size:16px;margin:22px 0 8px;padding-bottom:4px;border-bottom:1px solid #e5e7eb">{escape(value)}</h2>')
        elif kind == "ul":
            out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in value) + "</ul>")
        elif kind == "table":
            out.append(_table_html(value))
        elif kind == "chart":
            out.append(_chart_html(value))
        else:
            out.append("<p>" + "<br>".join(_inline(ln) for ln in value.splitlines()) + "</p>")
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'</head><body><div style="font-family:{_FONT};font-size:14px;line-height:1.5;color:#1f2937;max-width:680px">'
            + "\n".join(out) + "</div></body></html>")


def _table_text(t: EmailTable) -> str:
    widths = [max(len(c) for c in col) for col in zip(t.headers, *t.rows, strict=True)] if t.rows else [len(h) for h in t.headers]

    def line(cells: list[str]) -> str:
        return "  ".join(c.rjust(w) if a == "r" else c.ljust(w) for c, w, a in zip(cells, widths, t.align, strict=True)).rstrip()

    lead = t.title + (f" · {t.subtitle}" if t.subtitle else "")
    return "\n".join([lead, line(t.headers), "  ".join("-" * w for w in widths), *(line(r) for r in t.rows), *([t.note] if t.note else [])])


def _to_text(blocks: list[Block]) -> str:
    out: list[str] = []
    for kind, value in blocks:
        if kind == "h2":
            out.append(value)
        elif kind == "ul":
            out.append("\n".join(f"- {i.replace('**', '')}" for i in value))
        elif kind == "table":
            out.append(_table_text(value))
        elif kind == "chart":
            out.append(f"[Chart: {value.title}, shown in the HTML version]")
        else:
            out.append(value.replace("**", ""))
    return "\n\n".join(out)


def _to_markdown(blocks: list[Block]) -> str:
    def esc(v: str) -> str:
        return v.replace("|", "\\|")

    out: list[str] = []
    for kind, value in blocks:
        if kind == "h2":
            out.append(f"## {value}")
        elif kind == "ul":
            out.append("\n".join(f"- {i}" for i in value))
        elif kind == "chart":
            out.append(f"*[Chart: {value.title}]*")
        elif kind == "table":
            head = f"**{value.title}**" + (f" · {value.subtitle}" if value.subtitle else "")
            rule = "| " + " | ".join("---:" if a == "r" else "---" for a in value.align) + " |"
            rows = ["| " + " | ".join(esc(c) for c in r) + " |" for r in value.rows]
            out += [head, "\n".join(["| " + " | ".join(esc(h) for h in value.headers) + " |", rule, *rows])]
            if value.note:
                out.append(f"*{value.note}*")
        else:
            out.append(value)
    return "\n\n".join(out)


def _render(subject: str, blocks: list[Block]) -> RenderedEmail:
    return RenderedEmail(subject, _to_html(blocks), _to_text(blocks), _to_markdown(blocks))


def render_mom(mom: MoM) -> RenderedEmail:
    blocks: list[Block] = []
    for title, kind, items in (
        ("Summary", "p", [mom.summary] if mom.summary else []),
        ("Key discussion points", "ul", mom.discussion_points),
        ("Decisions made", "ul", mom.decisions),
        ("Action items", "ul", [a.line() for a in mom.action_items]),
        ("Open items", "ul", mom.open_items),
    ):
        if items:
            blocks += [("h2", title), (kind, items[0] if kind == "p" else items)]
    if mom.tables or mom.charts:
        blocks += [("h2", "Data"), *(("chart", c) for c in mom.charts), *(("table", t) for t in mom.tables)]
    if mom.attachments:
        blocks += [("h2", "Attachments"), ("ul", mom.attachments)]
    return _render(f"MoM · {mom.topic} · {mom.date}", blocks)


def render_ad_hoc(mail: AdHoc) -> RenderedEmail:
    return _render(mail.subject, _blocks(mail.body, mail.tables, mail.charts))

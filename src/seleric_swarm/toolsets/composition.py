"""Break an additive metric into the lines it is built from (catalogue ``formula.composition``).

The catalogue says, per metric, which metrics on the same view it is exactly the signed sum of — net profit =
contribution margin − ad spend; contribution margin = net sales − net COGS; net sales = gross − discounts −
returns − cancellations … (declared in the Cube model, checked day by day against the data by Agent_Core's
``scripts/verify_compositions.py``). This tool walks that tree, fetches every line in one query per period on the
metric's own view and basis, and checks the reconciliation itself, so a breakdown, a waterfall or a bridge between
two periods never mixes bases. Live 2026-10-09 (MS3-9781c608fc): a "net profit waterfall" assembled by hand from
commerce net sales, refund-date refunds and P&L costs showed a ₹1.6M "residual" on a P&L that reconciles to the
paisa.

Nothing here names a metric: the tree, labels and units all come from the catalogue definitions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import ArtifactProvenance
from seleric_swarm.services.mcp_query import DEFAULT_BRAND_ID, dimension_value
from seleric_swarm.toolsets import diagnosis, semantic
from seleric_swarm.toolsets.analytics import _refuse

_CALCULATION_VERSION = "composition.v1"
# A line is reconciled when the parts sum to it within this many currency units (float sums of paise rows).
_TOLERANCE = 0.05
_MAX_DEPTH = 4


@dataclass
class _Node:
    metric: str
    sign: int  # sign of this line inside its parent
    depth: int
    effective: int  # sign of this line inside the top metric (product of signs on the path)
    children: list[_Node] = field(default_factory=list)


def _composition(definition: dict[str, Any]) -> list[tuple[str, int]]:
    terms = ((definition or {}).get("formula") or {}).get("composition") or []
    out: list[tuple[str, int]] = []
    for t in terms:
        if isinstance(t, dict) and t.get("metric") and t.get("sign") in (1, -1):
            out.append((str(t["metric"]), int(t["sign"])))
    return out


def _tree(metric: str, definitions: dict[str, dict[str, Any]], depth: int) -> _Node:
    root = _Node(metric=metric, sign=1, depth=0, effective=1)
    stack = [root]
    while stack:
        node = stack.pop()
        if node.depth >= depth:
            continue
        for part, sign in _composition(definitions.get(node.metric, {})):
            child = _Node(metric=part, sign=sign, depth=node.depth + 1, effective=node.effective * sign)
            node.children.append(child)
            stack.append(child)
    return root


def _walk(node: _Node) -> list[_Node]:
    out = [node]
    for c in node.children:
        out.extend(_walk(c))
    return out


def _label(definitions: dict[str, dict[str, Any]], metric: str) -> str:
    return str((definitions.get(metric) or {}).get("display_name") or metric)


def _composed_metrics(definitions: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(m for m, d in definitions.items() if _composition(d))


def _window(
    ctx: RunContext[SelericDeps], start: datetime | None, end: datetime | None
) -> tuple[date, date, list[str]]:
    notes: list[str] = []
    window = ctx.deps.resolved_window
    if start is None and end is None and window is not None and window.start and window.end:
        return date.fromisoformat(window.start), date.fromisoformat(window.end), notes
    default = ctx.deps.as_of - timedelta(days=1)
    s, e = start or end or default, end or start or default
    if (pinned := semantic._pin_to_resolved_window(ctx, s, e)) is not None:
        s, e, note = pinned
        notes.append(note)
    a, b = diagnosis._inclusive_days(s, e)
    today = ctx.deps.as_of.date()
    if window is None and a >= today:
        # The question named no period and only the running day was asked for: its figures are a fraction of a
        # day, so the last complete day is broken down instead (live 2026-10-09: "net profit waterfall" → today).
        a = b = today - timedelta(days=1)
        notes.append(f"no period was named, so the last complete day {a} is shown ({today} is still in progress)")
    return a, b, notes


def _num(raw: Any) -> float | None:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


async def _totals(
    ctx: RunContext[SelericDeps], metrics: list[str], start: date, end: date, filters: dict[str, Any]
) -> tuple[dict[str, float], dict[str, Any] | None, list[str], str | None, str | None]:
    """Every line's total over [start, end] in one query (same view, same filters)."""
    values, args, dropped, error, currency, _ = await _totals_by(ctx, metrics, start, end, filters, None)
    return values, args, dropped, error, currency


async def _totals_by(
    ctx: RunContext[SelericDeps], metrics: list[str], start: date, end: date, filters: dict[str, Any],
    by: str | None,
) -> tuple[dict[str, float], dict[str, Any] | None, list[str], str | None, str | None, dict[str, dict[str, float]]]:
    """Totals, and with ``by`` every line per value of that dimension (one query either way)."""
    flt, dropped = diagnosis._filters_for(ctx, metrics[0], filters)
    args: dict[str, Any] = {
        "measures": list(metrics),
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
    }
    if by:
        args["dimensions"] = [by]
    flt = list(flt)
    if diagnosis._supports_brand(ctx, metrics[0]) and not any(f.get("dimension") == "brand_id" for f in flt):
        flt.append({"dimension": "brand_id", "operator": "equals", "values": [DEFAULT_BRAND_ID]})
    if flt:
        args["filters"] = flt
    result = await semantic._cached_metrics_query(ctx, args)
    if result.get("error"):
        return {}, None, dropped, str(result.get("error")), None, {}
    values: dict[str, float] = {}
    segments: dict[str, dict[str, float]] = {}
    for row in result.get("rows") or []:
        seg = str(dimension_value(row, by)) if by else ""
        for m in metrics:
            v = _num(row.get(m))
            if v is not None:
                values[m] = values.get(m, 0.0) + v
                if by:
                    segments.setdefault(seg, {})[m] = segments.get(seg, {}).get(m, 0.0) + v
    currency = str((result.get("provenance") or {}).get("currency") or "").strip() or None
    return values, args, dropped, None, currency, segments


def _reconcile(node: _Node, values: dict[str, float]) -> list[str]:
    """Lines whose parts do not sum to them (never hidden: a gap is reported, not absorbed)."""
    gaps: list[str] = []
    for n in _walk(node):
        if not n.children or n.metric not in values:
            continue
        if any(c.metric not in values for c in n.children):
            gaps.append(f"{n.metric}: a part has no value, so it cannot be reconciled")
            continue
        parts = sum(c.sign * values[c.metric] for c in n.children)
        if abs(values[n.metric] - parts) > _TOLERANCE:
            gaps.append(f"{n.metric} = {values[n.metric]:,.2f} but its parts sum to {parts:,.2f}")
    return gaps


def _fmt(v: float) -> str:
    return f"{v:,.2f}"


async def break_down_metric(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    compare_start: datetime | None = None,
    compare_end: datetime | None = None,
    filters: dict[str, str | list[str]] | None = None,
    depth: int = _MAX_DEPTH,
    by: str | None = None,
) -> ToolResult:
    """Break a metric into the lines it is built from — a P&L / waterfall / cost breakdown, or a bridge between two periods.

    Use for "break down", "waterfall", "what makes up", "reconcile", "walk from X to Y" and "how much of the
    change came from each component" questions about an additive money metric (profit, margin, net sales,
    costs). The lines come from the catalogue's verified composition of ``metric_id`` (e.g. net profit =
    contribution margin − ad spend, contribution margin = net sales − net COGS, net sales = gross sales −
    discounts − returns − cancellations), all on the metric's own view and date basis, so they reconcile to
    the total exactly; the result says so, or names any gap. ``period_start``/``period_end`` default to the
    question's period. Pass ``compare_start``/``compare_end`` for a bridge: each line's change and its signed
    contribution to the total's change. ``filters`` scopes every line alike (e.g. one channel). ``depth``
    limits how many levels are expanded. ``by`` (a dimension id the total's view carries) also splits every line
    by that dimension — a line the user asks to see apart (returns vs cancellations inside a deductions line, the
    P&L per channel) — each segment reconciling on its own.

    Present the lines in the order returned (top line first, deductions with their sign) and the subtotals as
    given; do not add lines from other metrics — they are on another basis and will not reconcile.
    """
    definitions = await diagnosis._all_definitions(ctx)
    if (unknown := await semantic._reject_unknown_metric(ctx, metric_id)) is not None:
        return unknown
    if not _composition(definitions.get(metric_id, {})):
        composed = _composed_metrics(definitions)
        hint = (
            " Metrics with a verified composition: "
            + ", ".join(f"{m} ({_label(definitions, m)})" for m in composed)
            if composed
            else ""
        )
        return _refuse(
            f"{metric_id} has no declared composition in the catalogue, so it cannot be broken into reconciling "
            f"lines.{hint}",
            error_code="UNSUPPORTED_QUERY",
            retryable=bool(composed),
        )
    switched_note = ""
    if filters:
        _, dropped_here = diagnosis._filters_for(ctx, metric_id, dict(filters))
        if dropped_here:
            # The slice lives at another grain (a product): the catalogue's grain twin of the total carries it and
            # has its own verified composition. Broken down on the total's view, the product's filter was dropped
            # and the brand P&L came back (live 2026-10-10: 'profit waterfall for Pawveralls Suspender Boots').
            grain_twins = getattr(ctx.deps.catalogue, "grain_twins_for", None)
            for twin in (grain_twins(metric_id) if grain_twins is not None else []):
                if _composition(definitions.get(twin, {})) and not diagnosis._filters_for(ctx, twin, dict(filters))[1]:
                    switched_note = (
                        f"{metric_id} cannot be sliced by {', '.join(dropped_here)}; broken down as its grain twin "
                        f"{twin} ({_label(definitions, twin)}), which carries that slice"
                    )
                    metric_id = twin
                    break
    root = _tree(metric_id, definitions, max(1, min(int(depth or _MAX_DEPTH), _MAX_DEPTH)))
    nodes = _walk(root)
    metrics = list(dict.fromkeys(n.metric for n in nodes))
    start, end, notes = _window(ctx, period_start, period_end)
    if switched_note:
        notes.insert(0, switched_note)
    today = ctx.deps.as_of.date()
    if end >= today:
        notes.append(f"{today} is still in progress; its figures are partial")
    filt = dict(filters or {})

    periods: list[tuple[str, date, date]] = [("period", start, end)]
    if compare_start is not None or compare_end is not None:
        c_start, c_end = diagnosis._inclusive_days(compare_start or compare_end, compare_end or compare_start)  # type: ignore[arg-type]
        if (c_end - c_start) != (end - start):
            notes.append(
                f"the comparison period {c_start}..{c_end} has {(c_end - c_start).days + 1} days and the period "
                f"{(end - start).days + 1}; totals are compared as they are"
            )
        periods.append(("compare", c_start, c_end))

    index = semantic._evidence_index(ctx)
    values_by: dict[str, dict[str, float]] = {}
    evidence_ids: list[str] = []
    currency: str | None = None
    dropped_any: list[str] = []
    for key, p_start, p_end in periods:
        values, args, dropped, error, cur = await _totals(ctx, metrics, p_start, p_end, filt)
        if args is None:
            return semantic._fetch_failure(f"break_down_metric({metric_id})", error)
        currency = currency or cur
        dropped_any += dropped
        values_by[key] = values
        tz = ctx.deps.as_of.tzinfo
        for m in metrics:
            if m not in values:
                continue
            unit = currency if (definitions.get(m) or {}).get("unit") not in ("count", "ratio") else None
            ev = EvidenceArtifact(
                metric_id=m, dimensions={}, grain="none", as_of=ctx.deps.as_of,
                period_start=datetime(p_start.year, p_start.month, p_start.day, tzinfo=tz),
                period_end=datetime(p_end.year, p_end.month, p_end.day, tzinfo=tz),
                value=values[m], unit=unit, source_query=args,
            )
            evidence_ids.append(semantic._put_evidence(
                ctx, ev, index=index, raw_id=f"raw:{m}:{p_start}:{p_end}:{sorted(filt.items())}",
                provenance=ArtifactProvenance(
                    calculation_version=_CALCULATION_VERSION,
                    source_metadata={"filters_applied": [f for f in (args.get("filters") or []) if isinstance(f, dict)]},
                ),
            ))
    if metric_id not in values_by["period"]:
        return _refuse(f"no data for {metric_id} over {start}..{end}", error_code="INSUFFICIENT_EVIDENCE")

    gaps = [g for vals in values_by.values() for g in _reconcile(root, vals)]
    cur_label = currency or ""
    lines: list[str] = []
    metrics_out: dict[str, float] = {}
    compare = values_by.get("compare")
    total_change = None
    if compare is not None and metric_id in compare:
        total_change = values_by["period"][metric_id] - compare[metric_id]
    header = f"| Line | {start}..{end} |" + (f" {periods[1][1]}..{periods[1][2]} | Change | Effect on {metric_id} |" if compare else "")
    lines.append(header)
    lines.append("| --- | ---: |" + (" ---: | ---: | ---: |" if compare else ""))
    for n in nodes:
        v = values_by["period"].get(n.metric)
        if v is None:
            continue
        indent = "  " * n.depth
        sign = "" if n.depth == 0 else ("+ " if n.sign > 0 else "− ")
        row = f"| {indent}{sign}{_label(definitions, n.metric)} ({n.metric}) | {_fmt(v)} |"
        metrics_out[f"{n.metric} | {start}..{end}"] = v
        if compare is not None and n.metric in compare:
            change = v - compare[n.metric]
            effect = n.effective * change
            row += f" {_fmt(compare[n.metric])} | {_fmt(change)} | {_fmt(effect)} |"
            metrics_out[f"{n.metric} | {periods[1][1]}..{periods[1][2]}"] = compare[n.metric]
            metrics_out[f"{n.metric} | change"] = change
            if n.depth:
                metrics_out[f"{n.metric} | effect on {metric_id}"] = effect
        lines.append(row)

    # The flat waterfall: the leaves in order, each with its sign in the top metric, end at the total.
    leaves = [n for n in nodes if not n.children and n.depth > 0 and n.metric in values_by["period"]]
    walk = " ".join(
        f"{'+' if n.effective > 0 else '−'} {_label(definitions, n.metric)} {_fmt(values_by['period'][n.metric])}"
        for n in leaves
    )
    reconciled = not gaps
    summary_parts = [
        f"{_label(definitions, metric_id)} ({metric_id}) over {start}..{end} = {_fmt(values_by['period'][metric_id])}"
        f" {cur_label}".rstrip()
        + (f"; {periods[1][1]}..{periods[1][2]} = {_fmt(compare[metric_id])}; change {_fmt(total_change)}"
           if compare is not None and total_change is not None else ""),
        "Reconciled: every line equals the signed sum of its parts."
        if reconciled
        else "NOT reconciled — " + "; ".join(gaps),
        "\n".join(lines),
        f"Waterfall (leaves → total): {walk} = {_fmt(values_by['period'][metric_id])}",
    ]
    if total_change is not None:
        movers = sorted(
            (n for n in leaves if n.metric in compare),  # type: ignore[operator]
            key=lambda n: -abs(n.effective * (values_by["period"][n.metric] - compare[n.metric])),  # type: ignore[index]
        )
        summary_parts.append(
            "Largest effects on the change: "
            + "; ".join(
                f"{_label(definitions, n.metric)} {_fmt(n.effective * (values_by['period'][n.metric] - compare[n.metric]))}"  # type: ignore[index]
                for n in movers[:5]
            )
            + f" (the effects of all leaves sum to {_fmt(total_change)})"
        )
    # A line the semantic layer declares a natural split for (Cube meta.split_by: deductions by refund class) is
    # opened into its parts under the line, the parts checked to sum to it — "how much went to returns and
    # cancellations" was answered with one deductions line.
    for n in nodes:
        split = (definitions.get(n.metric) or {}).get("split_by")
        if not split or n.metric not in values_by["period"] or split == by:
            continue
        _, p_args, _, _, _, parts = await _totals_by(ctx, [n.metric], start, end, filt, str(split))
        if p_args is None or len(parts) < 2:
            continue
        line_total = values_by["period"][n.metric]
        part_sum = sum(v.get(n.metric, 0.0) for v in parts.values())
        tz = ctx.deps.as_of.tzinfo
        shown = []
        for seg, vals in sorted(parts.items(), key=lambda kv: -abs(kv[1].get(n.metric, 0.0))):
            v = vals.get(n.metric, 0.0)
            shown.append(f"{seg} {_fmt(v)}")
            metrics_out[f"{n.metric} | {split}={seg}"] = v
            ev = EvidenceArtifact(
                metric_id=n.metric, dimensions={str(split): seg}, grain="none", as_of=ctx.deps.as_of,
                period_start=datetime(start.year, start.month, start.day, tzinfo=tz),
                period_end=datetime(end.year, end.month, end.day, tzinfo=tz),
                value=v, unit=currency, source_query=p_args,
            )
            evidence_ids.append(semantic._put_evidence(
                ctx, ev, index=index, raw_id=f"raw:{n.metric}:{start}:{end}:{split}={seg}",
                provenance=ArtifactProvenance(
                    calculation_version=_CALCULATION_VERSION,
                    source_metadata={"filters_applied": [f for f in (p_args.get("filters") or []) if isinstance(f, dict)]},
                ),
            ))
        summary_parts.append(
            f"Inside {_label(definitions, n.metric)} ({_fmt(line_total)}) by {split}: " + "; ".join(shown)
            + ("." if abs(part_sum - line_total) <= _TOLERANCE else f" — parts sum to {_fmt(part_sum)}, NOT the line.")
            + " Show these parts under the line."
        )
    if by:
        carried = set(ctx.deps.catalogue.supported_dimensions_for(metric_id)) if ctx.deps.catalogue.metrics else {by}
        if by not in carried:
            notes.append(f"{metric_id} cannot be split by {by} on its view; lines shown in total only")
        else:
            _, s_args, _, s_err, _, segs = await _totals_by(ctx, metrics, start, end, filt, by)
            if s_args is not None and segs:
                cols = [n for n in nodes if not n.children] + [root]
                head = f"| {by} | " + " | ".join(_label(definitions, n.metric) for n in cols) + " |"
                rows_txt = [head, "|" + " --- |" + " ---: |" * len(cols)]
                seg_gaps = 0
                tz = ctx.deps.as_of.tzinfo
                for seg, vals in sorted(segs.items(), key=lambda kv: -abs(kv[1].get(metric_id, 0.0))):
                    rows_txt.append(f"| {seg} | " + " | ".join(_fmt(vals.get(n.metric, 0.0)) for n in cols) + " |")
                    seg_gaps += bool(_reconcile(root, vals))
                    for n in cols:
                        if n.metric in vals:
                            metrics_out[f"{n.metric} | {by}={seg}"] = vals[n.metric]
                            ev = EvidenceArtifact(
                                metric_id=n.metric, dimensions={by: seg}, grain="none", as_of=ctx.deps.as_of,
                                period_start=datetime(start.year, start.month, start.day, tzinfo=tz),
                                period_end=datetime(end.year, end.month, end.day, tzinfo=tz),
                                value=vals[n.metric], unit=currency, source_query=s_args,
                            )
                            evidence_ids.append(semantic._put_evidence(
                                ctx, ev, index=index, raw_id=f"raw:{n.metric}:{start}:{end}:{by}={seg}",
                                provenance=ArtifactProvenance(
                                    calculation_version=_CALCULATION_VERSION,
                                    source_metadata={"filters_applied": [f for f in (s_args.get("filters") or []) if isinstance(f, dict)]},
                                ),
                            ))
                summary_parts.append(
                    f"By {by} ({start}..{end}; every segment's lines "
                    + ("reconcile" if not seg_gaps else f"do NOT reconcile in {seg_gaps} segment(s)") + "):\n"
                    + "\n".join(rows_txt)
                )
            elif s_err:
                notes.append(f"the split by {by} failed: {s_err[:160]}")
    if dropped_any:
        notes.append(f"filters not carried by this view were not applied: {', '.join(sorted(set(dropped_any)))}")
    if notes:
        summary_parts.append("Notes: " + "; ".join(notes))
    finding = Finding(
        finding_type="composition",
        statement=summary_parts[0],
        evidence_ids=evidence_ids,
        metrics=metrics_out,
    )
    finding_id = diagnosis._put_derived(ctx, "finding", finding, evidence_ids)
    ctx.deps.scratchpad.note(f"break_down_metric({metric_id}, {start}..{end}) → {'reconciled' if reconciled else 'GAP'}")
    return ToolResult(
        success=True,
        artifact_ids=[finding_id, *evidence_ids],
        summary="\n\n".join(summary_parts),
        provenance=ArtifactProvenance(
            evidence_ids=evidence_ids,
            calculation_version=_CALCULATION_VERSION,
            source_metadata={"finding_id": finding_id, "reconciled": reconciled, "currency": currency},
        ),
        warnings=gaps + notes,
    )

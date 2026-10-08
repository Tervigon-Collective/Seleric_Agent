"""ExplorationToolset — "what is worth knowing in this data?" in one call.

``explore_data`` is the composite the agent calls when the user wants the data
explored rather than one number fetched: "anything unusual this week?", "how
is the business doing?", "what should I look at?", or a drill-down into a
segment a previous exploration surfaced. It plans the subspace from catalogue
metadata (``exploration/space.py``), fetches through the same certified Cube
path ``query_metrics`` uses (rule 1; budgeted, cached), and hands the daily
series to the pure engine in ``exploration/engine.py``, which tests every
pattern, controls the false-discovery rate across all of them and ranks the
survivors. It does not call any other tool (rule 4).

Same split as ``toolsets/diagnosis.py`` and for the same reason: an
exploration fetches hundreds of daily rows across metrics and breakdowns, and
routing their evidence ids through the model corrupts them. Every reported
insight is written as a ``Finding`` backed by the ``EvidenceArtifact`` rows it
rests on; each one also carries typed follow-up probes (drill into a segment,
diagnose a move) so the next step of the walk is a concrete tool call.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import Finding
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.causal import diagnosis as dx
from seleric_swarm.conversations.contracts import ArtifactProvenance
from seleric_swarm.exploration import engine, space
from seleric_swarm.toolsets import diagnosis, semantic
from seleric_swarm.toolsets import policy_config as P

_CALCULATION_VERSION = "exploration.v1"
_FETCH_CONCURRENCY = 6

Depth = Literal["scan", "focus", "deep"]


def _refuse(summary: str, *, error_code: str = "INSUFFICIENT_EVIDENCE") -> ToolResult:
    return ToolResult(success=False, summary=summary, error_code=error_code, retryable=False)


def _window(
    ctx: RunContext[SelericDeps], period_start: datetime | None, period_end: datetime | None, notes: list[str]
) -> list[date] | ToolResult:
    """The complete days to explore: the question's period, else the last
    ``EXPLORE_DEFAULT_WINDOW_DAYS`` complete days. Today is in progress and is
    never part of a window it would be compared against."""
    today = ctx.deps.as_of.date()
    resolved = ctx.deps.resolved_window
    if period_start is None and period_end is None and resolved is not None and resolved.start and resolved.end:
        start, end = date.fromisoformat(resolved.start), date.fromisoformat(resolved.end)
    elif period_start is None and period_end is None:
        end = today - timedelta(days=1)
        start = end - timedelta(days=P.EXPLORE_DEFAULT_WINDOW_DAYS - 1)
    else:
        start_dt = period_start or period_end
        end_dt = period_end or period_start
        assert start_dt is not None and end_dt is not None
        if (pinned := semantic._pin_to_resolved_window(ctx, start_dt, end_dt)) is not None:
            start_dt, end_dt, note = pinned
            notes.append(note)
        start, end = start_dt.date(), end_dt.date()
        # "D 00:00 .. D+1 00:00" is one day with an exclusive end.
        if end > start and (end_dt.hour, end_dt.minute, end_dt.second, end_dt.microsecond) == (0, 0, 0, 0):
            end -= timedelta(days=1)
    if end < start:
        start, end = end, start
    if end >= today:
        if start >= today:
            return _refuse(
                "the requested period is still in progress; exploration compares complete days — "
                "use query_metrics(elapsed_only=True) for today so far",
                error_code="UNSUPPORTED_QUERY",
            )
        notes.append(f"{today.isoformat()} is still in progress and was left out")
        end = today - timedelta(days=1)
    n = (end - start).days + 1
    if n > P.EXPLORE_MAX_WINDOW_DAYS:
        return _refuse(
            f"the window {start}..{end} is {n} days; explore at most {P.EXPLORE_MAX_WINDOW_DAYS} days at a time",
            error_code="UNSUPPORTED_QUERY",
        )
    return [start + timedelta(days=i) for i in range(n)]


def _finite_stats(stats: dict[str, float], insight: engine.Insight) -> dict[str, float]:
    out = {k: float(v) for k, v in stats.items() if isinstance(v, (int, float)) and math.isfinite(float(v))}
    out["score"] = insight.score
    out["q_value"] = float(insight.q_value if insight.q_value is not None else 1.0)
    return out


def _summary(report: engine.ExplorationReport, explored: list[str], lineage: dict[str, dx.MetricMeta]) -> str:
    labels = ", ".join(dx._label(m, lineage) for m in explored)
    period = engine._period(report.window)
    lines = [
        (
            f"EXPLORATION of {labels} over {period}: {report.tested} patterns tested; {report.survived} held up "
            f"after false-discovery control at {int(P.EXPLORE_FDR * 100)}% and a minimum practical size."
        )
    ]
    if not report.insights:
        lines.append(
            "Nothing stood out: every movement found is within the normal window-to-window variation of these "
            "metrics. Say so plainly — that is the answer, not a failure."
        )
    else:
        lines.append("FINDINGS, strongest first (business language; keep each number as given):")
        for n, i in enumerate(report.insights, 1):
            tag = " [association, not a cause]" if i.classification == "ASSOCIATION" else ""
            lines.append(f"{n}. {i.statement}{tag}")
    if report.data_quality:
        lines.append("DATA NOTES: " + " ".join(report.data_quality))
    probes = [(n, f) for n, i in enumerate(report.insights, 1) for f in i.follow_ups]
    if probes:
        lines.append("NEXT PROBES (tool calls that would deepen a finding; run one if the question needs the why):")
        lines += [f"- for {n}: {f.tool}({f.args}) — {f.reason}" for n, f in probes[:8]]
    lines.append(
        "REPORTING RULES: these are observations of what moved, not causes — never write caused/drove/because "
        "of unless a diagnose_metric_change result says 'a cause'; 'moved together' findings are associations; "
        "report findings in the order given; no ids, snake_case labels, p-values or field names in the answer."
    )
    return "\n".join(lines)


async def explore_data(
    ctx: RunContext[SelericDeps],
    metric_ids: list[str] | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    filters: dict[str, str | list[str]] | None = None,
    depth: Depth = "focus",
) -> ToolResult:
    """Explore the data for what is unusual or worth knowing; returns ranked, tested findings.

    Use when the user wants the data explored rather than one value fetched —
    "anything unusual?", "how are we doing?", "what should I look at?", "dig
    into <segment>". With no ``metric_ids`` it starts from the catalogue's base
    metrics; pass ids to explore specific ones. ``period_start``/``period_end``
    default to the question's period, else the last 7 complete days; each
    window is compared with the one before it and with the variation of the
    windows before that. ``filters`` restricts the exploration to a segment
    (e.g. a follow-up probe's ``{dimension: value}``). ``depth``: "scan"
    (fastest, 3 metrics x 2 breakdowns), "focus" (default), "deep" (widest).

    Findings: window changes, trends, step changes, which segments a change
    sits in (and which moved against it), a segment far larger than the rest,
    and metrics that move together. Each passed a multiple-testing control; each
    comes with follow-up probes to run next.
    """
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, "", [])}
    for m in metric_ids or []:
        if (unknown := semantic._reject_unknown_metric(ctx, m)) is not None:
            return unknown
    n_metrics, n_dims, top_k = P.EXPLORE_DEPTH.get(depth, P.EXPLORE_DEPTH["focus"])
    notes: list[str] = []
    window = _window(ctx, period_start, period_end, notes)
    if isinstance(window, ToolResult):
        return window
    history = min(P.EXPLORE_HISTORY_WINDOWS, P.EXPLORE_MAX_SPAN_DAYS // len(window) - 1)
    if history < P.EXPLORE_HISTORY_WINDOWS:
        notes.append(f"only {max(history, 0)} earlier windows fit the history limit, so 'normal variation' is thin")
    history = max(history, 1)
    wins = engine.windows(window, history)
    span_start, span_end = wins[-1][0], window[-1]

    definitions = await diagnosis._all_definitions(ctx)
    lineage = dx.lineage_from_definitions(definitions)
    catalogue = ctx.deps.catalogue
    if metric_ids:
        metrics = list(dict.fromkeys(metric_ids))[: P.EXPLORE_DEPTH["deep"][0]]
        if len(metrics) < len(set(metric_ids)):
            notes.append(f"explored the first {len(metrics)} of the metrics named")
    else:
        metrics = space.headline_metrics(catalogue, lineage, n_metrics)
    if not metrics:
        return _refuse("the catalogue lists no additive base metrics to explore; name the metrics to explore")

    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def guarded(coro: Any) -> Any:
        async with sem:
            return await coro

    series_jobs = [(m, None) for m in metrics]
    seg_jobs = [
        (m, d)
        for m in metrics
        if lineage.get(m, dx.MetricMeta(m)).additive
        for d in space.plan_dimensions(catalogue, m, n_dims, exclude=set(filters))
    ]
    jobs = series_jobs + seg_jobs
    results = await asyncio.gather(*(
        guarded(diagnosis._fetch_daily(ctx, m, span_start, span_end, filters, dimension=d)) for m, d in jobs
    ))
    # The share of the whole business the explored segment is (impact): one
    # unfiltered total per metric over the current window.
    unfiltered = (
        await asyncio.gather(*(guarded(diagnosis._fetch_daily(ctx, m, window[0], window[-1], {})) for m in metrics))
        if filters else []
    )

    quality: list[str] = []
    series: dict[str, dict[date, float]] = {}
    args_for: dict[tuple[str, str | None], dict[str, Any]] = {}
    segments: dict[str, dict[str, dict[str, dict[date, float]]]] = {}
    for (m, d), (res, args) in zip(jobs, results, strict=True):
        if args is None:
            if d is None and metric_ids and m in metric_ids and len(metrics) == 1:
                return semantic._fetch_failure(f"explore_data({m})", res.get("error"))
            quality.append(f"{dx._label(m, lineage)}{' by ' + d if d else ''}: fetch failed")
            continue
        if d is None:
            if res.get("_dropped_filters"):
                quality.append(
                    f"{dx._label(m, lineage)} cannot be filtered by {', '.join(res['_dropped_filters'])}; used unfiltered"
                )
            if parsed := diagnosis._parse_series(res, m):
                series[m] = parsed
                args_for[(m, None)] = args
            continue
        if len(res.get("rows") or []) > P.EXPLORE_MAX_SEGMENT_ROWS:
            quality.append(f"{dx._label(m, lineage)} by {d.replace('_', ' ')} has too many segments to screen")
            continue
        if len(parsed_segments := diagnosis._parse_segments(res, m, d)) >= 2:
            segments.setdefault(m, {})[d] = parsed_segments
            args_for[(m, d)] = args
    if not series:
        return _refuse(f"no daily data for {', '.join(metrics)} over {span_start}..{span_end}")
    share: dict[str, float] = {}
    for m, (res, args) in zip(metrics, unfiltered, strict=False):
        meta = lineage.get(m, dx.MetricMeta(m))
        whole = engine.window_value(diagnosis._parse_series(res, m), window, True) if args else None
        part = engine.window_value(series.get(m, {}), window, True)
        if meta.additive and whole and part is not None and whole > 0:
            share[m] = max(0.0, min(1.0, part / whole))

    inp = engine.ExplorationInput(
        window=window, series=series, lineage=lineage, segments=segments, history_windows=history,
        subspace_share=share, filters=filters, top_k=top_k,
        scope_tokens=frozenset(t for d in space.scope_dimensions(catalogue) for t in d.lower().split("_") if t),
    )
    report = await asyncio.to_thread(engine.explore, inp)
    report.data_quality = [*notes, *quality, *report.data_quality]

    # ---- evidence + findings ---------------------------------------------------------
    tz = ctx.deps.as_of.tzinfo
    index = semantic._evidence_index(ctx)

    def cite(metric: str, days: tuple[date, ...] | list[date], dimension: str | None = None, segment: str | None = None) -> list[str]:
        args = args_for.get((metric, dimension))
        if args is None:
            return []
        values = segments[metric][dimension].get(segment or "", {}) if dimension else series.get(metric, {})
        return diagnosis._evidence_rows(
            ctx, metric, values, list(days), args, index, tz, {dimension: segment} if dimension and segment else None
        )

    # The current window of every explored metric is cited even when nothing
    # stood out, so "nothing unusual; revenue was X" is itself grounded.
    evidence_ids: list[str] = [eid for m in series for eid in cite(m, window)]
    finding_ids: list[str] = []
    for i in report.insights:
        ids = cite(i.metric, i.days)
        if i.related_metric:
            ids += cite(i.related_metric, i.days)
        for seg in i.segments:
            ids += cite(i.metric, i.days, i.dimension, seg)
        ids = list(dict.fromkeys(ids))
        if not ids:
            continue
        evidence_ids += ids
        finding = Finding(
            finding_type=f"exploration.{i.kind}", statement=i.statement, evidence_ids=ids,
            metrics=_finite_stats(i.stats, i),
        )
        finding_ids.append(diagnosis._put_derived(ctx, "finding", finding, ids))
    evidence_ids = list(dict.fromkeys(evidence_ids))
    if not evidence_ids:
        return _refuse("the exploration produced no citable evidence")

    ctx.deps.scratchpad.note(
        f"explore_data({', '.join(metrics)}, {window[0]}..{window[-1]}"
        + (f", filters={filters}" if filters else "")
        + f") → {len(report.insights)} findings from {report.tested} tests"
    )
    return ToolResult(
        success=True,
        artifact_ids=[*finding_ids, *evidence_ids],
        summary=_summary(report, list(series), lineage),
        provenance=ArtifactProvenance(
            evidence_ids=evidence_ids, calculation_version=_CALCULATION_VERSION,
            source_metadata={"exploration": report.as_dict(), "finding_ids": finding_ids, "metrics": list(series)},
        ),
        warnings=list(report.data_quality),
    )

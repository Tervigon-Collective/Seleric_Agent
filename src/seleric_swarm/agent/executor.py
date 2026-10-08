"""Deterministic execution of a templated plan's data steps (2026-10-07).

For comparison plans the queries are fully determined once the planner's slots are
resolved: rank the entities, fetch the same entities in both windows (the same
elapsed hours when one window is today), map the amounts to the breakdowns asked
for. Before this the agent issued those 10-30 ``query_metrics`` calls itself over
4-8 LLM turns (15-55k input tokens each), skipped some, and compared mismatched
windows. Here code runs them in parallel through the same ``query_metrics`` tool —
same validation, same evidence store — computes per-day figures and changes, and
records the derived numbers as a citable Finding. The agent then writes the
explanation from a compact table and fetches only what is missing.

Nothing here names a metric: ids, additivity and volume metrics come from the plan
and the catalogue. Fail-open: any error returns no prefetch and the mission runs
exactly as before.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from seleric_swarm.agent.artifacts import Finding
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.plan import MissionPlan
from seleric_swarm.agent.progress import emit_progress, tool_label
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.elapsed import ELAPSED_KEY, completed_hours, covers_in_progress_day, same_span
from seleric_swarm.toolsets import semantic

_log = logging.getLogger("seleric.agent.executor")

_PARALLEL = 6
_MAX_TABLE_ROWS = 80
_CANDIDATE_FACTOR = 3


@dataclass
class Prefetch:
    text: str
    evidence_ids: list[str] = field(default_factory=list)
    finding_id: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)


def _ctx(deps: SelericDeps) -> Any:
    return SimpleNamespace(deps=deps, run_step=None)


def _at(day: date, as_of: datetime) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=as_of.tzinfo)


def _payloads(deps: SelericDeps, artifact_ids: list[str], *, elapsed: bool = False) -> list[dict[str, Any]]:
    """The evidence rows of one query on the basis it asked for: full periods, or
    only the hours elapsed today (``elapsed``). query_metrics adds a same-hours
    companion to a complete window when the mission also covers the running day;
    keyed like the full row, it overwrote it (golden Q6 2026-10-08: Sep 1-8 net
    sales 696,778 shown as the 00:00-02:00 slice, 31,102)."""
    out = []
    for aid in artifact_ids:
        payload = getattr(deps.artifact_store.get(aid), "payload", None)
        if isinstance(payload, dict) and (ELAPSED_KEY in (payload.get("dimensions") or {})) == elapsed:
            out.append(payload)
    return out


def _with_named_values(deps: SelericDeps, metric_id: str, dimensions: dict[str, Any] | None) -> dict[str, Any] | None:
    """The values the question names (the scope's value_filters, kept by the understanding) constrain every
    pre-fetched query, on a dimension of the value that the metric carries — query_metrics fits a conformed
    sibling or grain twin. A dimension the step already breaks down or filters by is left alone. Live
    2026-10-08: "Which Meta campaigns performed best…" ranked every campaign, Google's first, and the answer
    waived the value instead of filtering."""
    dims = dict(dimensions or {})
    catalogue = deps.catalogue
    for vf in getattr(deps.required_scope, "value_filters", ()) or ():
        if not vf.values or any(d in dims for d in vf.dimensions):
            continue
        supported = set(catalogue.supported_dimensions_for(metric_id))
        ordered = sorted(vf.dimensions, key=lambda d: (d not in supported, d))
        dim = next((d for d in ordered if catalogue.carries(metric_id, d)), None)
        if dim is not None:
            dims[dim] = vf.values[0] if len(vf.values) == 1 else list(vf.values)
    return dims or None


async def _query(deps: SelericDeps, gate: asyncio.Semaphore, **kwargs: Any) -> Any:
    kwargs["dimensions"] = _with_named_values(deps, kwargs["metric_id"], kwargs.get("dimensions"))
    async with gate:
        return await semantic.query_metrics(_ctx(deps), **kwargs)


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}" if abs(value) >= 1 else f"{value:.4f}"


def _change(before: float | None, after: float | None) -> float | None:
    if before in (None, 0) or after is None:
        return None
    return (after - before) / abs(before) * 100


_SINGLE_WINDOW_SHAPES = frozenset({"lookup", "trend", "breakdown"})


async def execute_plan(
    plan: MissionPlan | None,
    deps: SelericDeps,
    *,
    windows: list[tuple[date, date]],
    as_of: datetime,
    grain: str | None = None,
) -> Prefetch | None:
    """Run the plan's data steps when its shape has a deterministic template.

    Two-window comparisons run the rank-then-compare template. Single-window shapes
    (lookup, trend, breakdown) fetch every planned metric at the planned breakdown in
    parallel, so the agent's first turn already holds the numbers and only writes
    the answer. Diagnosis and funnels are one composite tool call each and are left
    to the agent."""
    if plan is None:
        return None
    two_windows = plan.shape in ("entity_comparison", "period_comparison") and len(windows) >= 2
    if not two_windows and plan.shape not in _SINGLE_WINDOW_SHAPES:
        return None
    started = time.perf_counter()
    labels = dict.fromkeys(tool_label(step.tool) for step in plan.steps)
    emit_progress(deps.mission_id, "agent.stage", "; ".join(labels), {"stage": "prefetch"})
    try:
        if two_windows:
            prefetch = await _execute(plan, deps, windows=windows, as_of=as_of, started=started)
        else:
            window = windows[0] if windows else (as_of.date(), as_of.date())
            prefetch = await _execute_single(plan, deps, window=window, as_of=as_of, grain=grain, started=started)
    except Exception:
        _log.warning("plan_execution_failed", exc_info=True)
        return None
    if prefetch is not None and (units := _units_line(plan, deps)):
        prefetch.text = f"{prefetch.text}\n{units}"
    return prefetch


def _units_line(plan: MissionPlan, deps: SelericDeps) -> str:
    """The catalogue unit of every prefetched metric. The fetched values are bare
    numbers; without their unit the model guessed the currency (labelled INR figures
    as USD, golden Q2 2026-10-08)."""
    raw = {m.id: (m.raw or {}) for m in deps.catalogue.metrics}
    metrics = list(dict.fromkeys(metric for step in plan.steps for metric in step.metric_ids if metric in raw))
    units = {metric: str(raw[metric].get("unit") or "").strip() for metric in metrics}
    units = {metric: unit for metric, unit in units.items() if unit}
    if not units:
        return ""
    line = "Units (catalogue): " + ", ".join(f"{metric}={unit}" for metric, unit in units.items())
    line += ". Label every figure with its unit"
    currencies = {str(raw[metric].get("currency_default") or "").strip() for metric in metrics} - {""}
    if len(currencies) == 1:
        line += f"; the footer's Currency is {currencies.pop()}"
    return line + "."


async def _execute_single(
    plan: MissionPlan,
    deps: SelericDeps,
    *,
    window: tuple[date, date],
    as_of: datetime,
    grain: str | None,
    started: float,
) -> Prefetch | None:
    step = next((s for s in plan.steps if s.tool == "query_metrics" and s.metric_ids), None)
    if step is None:
        return None
    if plan.shape == "trend":
        grain = grain if grain in ("day", "week", "month") else "day"
    elif grain not in ("day", "week", "month"):
        grain = "none"
    gate = asyncio.Semaphore(_PARALLEL)
    metrics = list(dict.fromkeys(step.metric_ids))
    jobs = []
    for metric in metrics:
        # query_metrics answers a conformed sibling / the grain twin; only a slice nothing carries is dropped
        dims = {d: "" for d in step.dimensions if deps.catalogue.carries(metric, d)}
        jobs.append(
            _query(
                deps, gate, metric_id=metric, dimensions=dims or None, grain=grain,
                period_start=_at(window[0], as_of), period_end=_at(window[1], as_of),
            )
        )
    results = await asyncio.gather(*jobs, return_exceptions=True)
    evidence_ids: list[str] = []
    derived: dict[str, float] = {}
    lines: list[str] = []
    failed: list[str] = []
    for metric, result in zip(metrics, results, strict=True):
        if isinstance(result, BaseException) or not getattr(result, "success", False):
            reason = "" if isinstance(result, BaseException) else f" ({str(result.summary)[:160]})"
            failed.append(f"{metric}{reason}")
            continue
        evidence_ids += result.artifact_ids
        lines.append(f"- {result.summary}")
        for payload in _payloads(deps, result.artifact_ids):
            if payload.get("value") is None:
                continue
            labels = [f"{k}={v}" for k, v in (payload.get("dimensions") or {}).items() if k != ELAPSED_KEY]
            if grain != "none":
                labels.append(str(payload.get("period_start", ""))[:10])
            derived[" | ".join([metric, *labels])] = round(float(payload["value"]), 4)
    if not lines:
        return None
    finding_id = _store_finding(
        deps, evidence_ids, derived, None,
        finding_type="prefetched_lookup",
        statement=f"Values fetched by the planner's executor for {window[0]}..{window[1]}.",
    )
    head = [
        "[prefetched data — already fetched by the planner's executor; do not re-fetch it]",
        f"Window {window[0]}..{window[1]}" + (f", grain={grain}" if grain != "none" else "") + ":",
    ]
    tail = []
    if failed:
        tail.append("Could not fetch: " + "; ".join(failed) + " — say so, or fetch them another way.")
    if finding_id:
        tail.append(f"Cite finding_ids=[{finding_id}] for every figure above; it links all the evidence.")
    stats = {
        "queries": len(jobs),
        "rows": len(derived),
        "failed": len(failed),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
    _log.info("plan_executed %s", stats)
    return Prefetch(
        text="\n".join([*head, *lines, *tail]),
        evidence_ids=list(dict.fromkeys(evidence_ids)),
        finding_id=finding_id,
        stats=stats,
    )


async def _execute(
    plan: MissionPlan, deps: SelericDeps, *, windows: list[tuple[date, date]], as_of: datetime, started: float
) -> Prefetch | None:
    catalogue = deps.catalogue
    gate = asyncio.Semaphore(_PARALLEL)
    # Entities are ranked in the window the question names first; the change always
    # runs from the earlier window to the later one. "This week compared to last week"
    # resolves to [this week, last week], and measuring last week against this week
    # inverted every sign (live 2026-10-08 MS3-18085c0092: "net sales fell from
    # 696,778 to 767,584", MER "dropped 34%" while it rose 1.11 -> 1.70).
    rank_window = windows[0]
    reference, comparison = sorted(windows[:2])
    today_running = covers_in_progress_day(comparison[0], comparison[1], as_of)
    cmp_days = (comparison[1] - comparison[0]).days + 1
    # A period to date against the period before it compares the same span
    # (``same_span``). Counting only the elapsed hours of EVERY day kept 16 of 24
    # hours of six full days.
    span = same_span(reference, comparison, as_of)
    to_date = span is not None
    if span is not None:
        reference = span
    ref_days = (reference[1] - reference[0]).days + 1
    per_day = ref_days != cmp_days
    entity_step = plan.steps[0] if plan.shape == "entity_comparison" else None
    entity = entity_step.dimensions[0] if entity_step and entity_step.dimensions else None
    compare_step = next((s for s in plan.steps if s.uses_entities_from_step or plan.shape == "period_comparison"), None)
    metrics = list(dict.fromkeys(compare_step.metric_ids if compare_step else []))
    if not metrics:
        return None
    evidence_ids: list[str] = []
    entities: list[str] = []

    if entity_step is not None and entity:
        rank = entity_step.metric_ids[0]
        limit = _limit(entity_step.ranking)
        volume = catalogue.volume_metric_for(rank)
        dims: dict[str, Any] = {entity: ""}
        if volume:
            # A ratio's leaders among entities with real volume: take the largest by
            # volume first, then rank those by the ratio.
            by_volume = await _query(
                deps, gate, metric_id=volume, dimensions=dims,
                period_start=_at(rank_window[0], as_of), period_end=_at(rank_window[1], as_of),
                order="desc", limit=limit * _CANDIDATE_FACTOR,
            )
            evidence_ids += by_volume.artifact_ids
            pool = [p["dimensions"].get(entity) for p in _payloads(deps, by_volume.artifact_ids)]
            dims = {entity: [p for p in pool if p]}
        ranked = await _query(
            deps, gate, metric_id=rank, dimensions=dims,
            period_start=_at(rank_window[0], as_of), period_end=_at(rank_window[1], as_of),
            **({} if volume else {"order": "desc", "limit": limit}),
        )
        evidence_ids += ranked.artifact_ids
        rows = sorted(
            (p for p in _payloads(deps, ranked.artifact_ids) if p.get("value") is not None),
            key=lambda p: p["value"],
            reverse=True,
        )
        entities = [str(p["dimensions"][entity]) for p in rows if p["dimensions"].get(entity)][:limit]
        if not entities:
            return None

    def dims_for() -> dict[str, Any] | None:
        return {entity: entities} if entity else None

    def parts(start: date, end: date, additive: bool) -> list[tuple[date, date, bool]]:
        """(start, end, elapsed) queries whose sum is the window's value on the basis."""
        if not additive or not today_running:
            return [(start, end, False)]
        if not to_date:
            return [(start, end, True)]
        whole = [(start, end - timedelta(days=1), False)] if end > start else []
        return [*whole, (end, end, True)]

    jobs = []
    for metric in metrics:
        additive = catalogue.aggregation_for(metric) == "additive"
        for label, (start, end) in (("ref", reference), ("cmp", comparison)):
            for part_start, part_end, elapsed in parts(start, end, additive):
                jobs.append(
                    (
                        metric,
                        label,
                        elapsed,
                        _query(
                            deps, gate, metric_id=metric, dimensions=dims_for(),
                            period_start=_at(part_start, as_of), period_end=_at(part_end, as_of),
                            elapsed_only=elapsed,
                        ),
                    )
                )
    results = await asyncio.gather(*(job for *_, job in jobs), return_exceptions=True)
    values: dict[tuple[str, str, str], float] = {}
    broken: set[tuple[str, str]] = set()
    for (metric, label, elapsed, _), result in zip(jobs, results, strict=True):
        if isinstance(result, BaseException) or not getattr(result, "success", False):
            broken.add((metric, label))
            continue
        evidence_ids += result.artifact_ids
        for payload in _payloads(deps, result.artifact_ids, elapsed=elapsed):
            if payload.get("value") is None:
                continue
            key = str(payload["dimensions"].get(entity, "")) if entity else ""
            values[(key, metric, label)] = values.get((key, metric, label), 0.0) + float(payload["value"])
    # A window with a failed part has no value: half a span is not the span.
    values = {k: v for k, v in values.items() if (k[1], k[2]) not in broken}
    # A metric without an hourly series cannot be cut to the hour: compare it over
    # the span's whole days and say so (live 2026-10-08: net profit came back
    # "unavailable" for both months rather than on whole days).
    whole_days: list[str] = []
    if to_date:
        retry = sorted({metric for metric, _label in broken if catalogue.aggregation_for(metric) == "additive"})
        fallback = await asyncio.gather(*(
            _query(deps, gate, metric_id=metric, dimensions=dims_for(),
                   period_start=_at(start, as_of), period_end=_at(end, as_of))
            for metric in retry for start, end in (reference, comparison)
        ), return_exceptions=True)
        for i, metric in enumerate(retry):
            pair = fallback[2 * i: 2 * i + 2]
            if any(isinstance(r, BaseException) or not getattr(r, "success", False) for r in pair):
                continue
            for label, result in zip(("ref", "cmp"), pair, strict=True):
                evidence_ids += result.artifact_ids
                broken.discard((metric, label))
                for payload in _payloads(deps, result.artifact_ids):
                    if payload.get("value") is not None:
                        key = str(payload["dimensions"].get(entity, "")) if entity else ""
                        values[(key, metric, label)] = float(payload["value"])
            whole_days.append(metric)
    failed = [f"{metric} ({label})" for metric, label in sorted(broken)]
    if per_day:
        # Windows of different length compare per day.
        for (key, metric, label), value in list(values.items()):
            if catalogue.aggregation_for(metric) == "additive":
                values[(key, metric, label)] = value / (ref_days if label == "ref" else cmp_days)

    mapping_lines = await _mapping(
        plan, deps, gate, entity, entities, comparison, as_of, today_running and not to_date, evidence_ids
    )

    keys = entities if entity else [""]
    per = " (per day)" if per_day else ""
    table = ["| " + (f"{entity} | " if entity else "")
             + f"metric | {reference[0]}..{reference[1]}{per} | {comparison[0]}..{comparison[1]}{per} | change |",
             "| " + ("--- | " if entity else "") + "--- | ---: | ---: | ---: |"]
    derived: dict[str, float] = {}
    for key in keys:
        for metric in metrics:
            before = values.get((key, metric, "ref"))
            after = values.get((key, metric, "cmp"))
            if before is None and after is None:
                continue
            change = _change(before, after)
            label = f"{key} | {metric}" if entity else metric
            for name, number in (("ref", before), ("cmp", after), ("change_pct", change)):
                if number is not None:
                    derived[f"{label} | {name}"] = round(number, 4)
            table.append(
                f"| {label} | {_fmt(before)} | {_fmt(after)} | "
                f"{'n/a' if change is None else f'{change:+.1f}%'} |"
            )
    if len(table) <= 2:
        return None
    hours = completed_hours(as_of)
    if to_date:
        basis = (
            f"Same span, like for like: the earlier window is cut to the {cmp_days} days the later one "
            f"has run, its last day counted to {hours:02d}:00 like today; additive metrics are totals "
            "over that span. Ratios are as reported over whole days (today only the hours so far)."
        )
    elif today_running:
        basis = (
            "Additive metrics count only the hours elapsed today on every day "
            f"(query_metrics elapsed_only){' and are shown per day' if per_day else ''} — like for like. "
            "Ratios are as reported: the earlier window's ratio covers full days, today's only the hours "
            "so far, so explain ratio moves from the additive columns, not from the ratio alone."
        )
    else:
        basis = f"Additive metrics are shown {'per day' if per_day else 'as totals'}; ratios are as reported."
    finding_id = _store_finding(
        deps, evidence_ids, derived, entity,
        statement=(
            f"Comparison{' by ' + entity if entity else ''} computed from the planner's prefetched evidence: "
            f"ref = {reference[0]}..{reference[1]}, cmp = {comparison[0]}..{comparison[1]}, "
            f"change_pct = (cmp - ref) / |ref|. {basis}"
        ),
    )
    head = [
        "[prefetched data — already fetched by the planner's executor; do not re-fetch it]",
        f"Earlier window {reference[0]}..{reference[1]} vs later window {comparison[0]}..{comparison[1]}; "
        f"change is from the earlier to the later. {basis}",
    ]
    if entity:
        head.append(f"Entities: the top {len(entities)} {entity} values (planner step 1), in rank order.")
    body = table[: _MAX_TABLE_ROWS + 2]
    tail = []
    if len(table) > _MAX_TABLE_ROWS + 2:
        tail.append(f"(+{len(table) - _MAX_TABLE_ROWS - 2} more rows in finding {finding_id})")
    if mapping_lines:
        tail += ["", *mapping_lines]
    if whole_days:
        tail.append(
            f"{', '.join(whole_days)}: no hourly series, so compared over the span's whole days — "
            f"the earlier window's last day is complete while today is not; say so beside these rows."
        )
    if failed:
        tail.append("Could not fetch: " + ", ".join(failed) + " — say so, or fetch them another way.")
    # One id to cite: the finding carries every evidence id behind it. Listing dozens of
    # 32-char ids cost tokens and the model mis-copied them (live 2026-10-07).
    tail.append(f"Cite finding_ids=[{finding_id}] for every figure above; it links all the evidence.")
    stats = {
        "queries": len(jobs) + (2 if entity_step else 0),
        "rows": len(table) - 2,
        "failed": len(failed),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
    _log.info("plan_executed %s", stats)
    return Prefetch(
        text="\n".join([*head, *body, *tail]),
        evidence_ids=list(dict.fromkeys(evidence_ids)),
        finding_id=finding_id,
        stats=stats,
    )


def _limit(ranking: str) -> int:
    """The plan's limit=N (written by compose_plan), else 10."""
    marker = "limit="
    if marker in ranking:
        digits = ""
        for ch in ranking.split(marker, 1)[1]:
            if not ch.isdigit():
                break
            digits += ch
        if digits:
            return max(1, int(digits))
    return 10


async def _mapping(
    plan: MissionPlan,
    deps: SelericDeps,
    gate: asyncio.Semaphore,
    entity: str | None,
    entities: list[str],
    comparison: tuple[date, date],
    as_of: datetime,
    today_running: bool,
    evidence_ids: list[str],
) -> list[str]:
    """The plan's breakdown step (amounts mapped to the dimensions asked for)."""
    step = next(
        (s for s in plan.steps if s.dimensions and any(d != entity for d in s.dimensions)), None
    )
    if step is None:
        return []
    breakdown = [d for d in step.dimensions if d != entity]
    lines = [f"Mapping for {comparison[0]}..{comparison[1]} by {', '.join([*([entity] if entity else []), *breakdown])}:"]
    for metric in step.metric_ids:
        dims: dict[str, Any] = {d: "" for d in breakdown if deps.catalogue.carries(metric, d)}
        if not dims:
            continue
        if entity:
            dims[entity] = entities
        elapsed_only = bool(today_running and deps.catalogue.aggregation_for(metric) == "additive")
        result = await _query(
            deps, gate, metric_id=metric, dimensions=dims,
            period_start=_at(comparison[0], as_of), period_end=_at(comparison[1], as_of),
            elapsed_only=elapsed_only,
        )
        if not getattr(result, "success", False):
            lines.append(f"- {metric}: not available ({result.summary[:120]})")
            continue
        evidence_ids += result.artifact_ids
        rows = []
        for payload in _payloads(deps, result.artifact_ids, elapsed=elapsed_only):
            labels = ", ".join(f"{k}={v}" for k, v in payload["dimensions"].items() if k != ELAPSED_KEY)
            rows.append(f"  - {labels}: {_fmt(payload.get('value'))}")
        lines.append(f"- {metric} ({len(rows)} rows):")
        lines += rows[:25]
        if len(rows) > 25:
            lines.append(f"  - … {len(rows) - 25} more rows in evidence")
    return lines if len(lines) > 1 else []


def _store_finding(
    deps: SelericDeps,
    evidence_ids: list[str],
    derived: dict[str, float],
    entity: str | None,
    *,
    finding_type: str = "prefetched_comparison",
    statement: str | None = None,
) -> str | None:
    if not evidence_ids or not derived:
        return None
    finding = Finding(
        finding_type=finding_type,
        statement=statement or (
            f"Per-day comparison{' by ' + entity if entity else ''} computed from the planner's "
            "prefetched evidence (additive metrics per day over the same elapsed hours; ratios as reported)."
        ),
        evidence_ids=list(dict.fromkeys(evidence_ids)),
        metrics=derived,
    )
    artifact = deps.artifact_store.put(
        Artifact(
            workspace_id=deps.principal.workspace_id,
            artifact_type="finding",
            payload=finding.model_dump(mode="json"),
            classification="derived",
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            provenance=ArtifactProvenance(evidence_ids=list(dict.fromkeys(evidence_ids)), calculation_version="executor.v1"),
            mission_id=deps.mission_id,
        )
    )
    return artifact.id

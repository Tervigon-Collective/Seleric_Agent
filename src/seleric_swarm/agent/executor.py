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
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

from seleric_swarm.agent.artifacts import Finding
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.plan import MissionPlan
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.elapsed import ELAPSED_KEY, covers_in_progress_day
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


def _payloads(deps: SelericDeps, artifact_ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for aid in artifact_ids:
        payload = getattr(deps.artifact_store.get(aid), "payload", None)
        if isinstance(payload, dict):
            out.append(payload)
    return out


async def _query(deps: SelericDeps, gate: asyncio.Semaphore, **kwargs: Any) -> Any:
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


async def execute_plan(
    plan: MissionPlan | None,
    deps: SelericDeps,
    *,
    windows: list[tuple[date, date]],
    as_of: datetime,
) -> Prefetch | None:
    """Run the plan's data steps when its shape has a deterministic template."""
    if plan is None or plan.shape not in ("entity_comparison", "period_comparison") or len(windows) < 2:
        return None
    started = time.perf_counter()
    try:
        return await _execute(plan, deps, windows=windows, as_of=as_of, started=started)
    except Exception:
        _log.warning("plan_execution_failed", exc_info=True)
        return None


async def _execute(
    plan: MissionPlan, deps: SelericDeps, *, windows: list[tuple[date, date]], as_of: datetime, started: float
) -> Prefetch | None:
    catalogue = deps.catalogue
    gate = asyncio.Semaphore(_PARALLEL)
    reference, comparison = windows[0], windows[1]
    today_running = covers_in_progress_day(comparison[0], comparison[1], as_of)
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
                period_start=_at(reference[0], as_of), period_end=_at(reference[1], as_of),
                order="desc", limit=limit * _CANDIDATE_FACTOR,
            )
            evidence_ids += by_volume.artifact_ids
            pool = [p["dimensions"].get(entity) for p in _payloads(deps, by_volume.artifact_ids)]
            dims = {entity: [p for p in pool if p]}
        ranked = await _query(
            deps, gate, metric_id=rank, dimensions=dims,
            period_start=_at(reference[0], as_of), period_end=_at(reference[1], as_of),
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

    jobs = []
    for metric in metrics:
        additive = catalogue.aggregation_for(metric) == "additive"
        for label, (start, end) in (("ref", reference), ("cmp", comparison)):
            jobs.append(
                (
                    metric,
                    label,
                    _query(
                        deps, gate, metric_id=metric, dimensions=dims_for(),
                        period_start=_at(start, as_of), period_end=_at(end, as_of),
                        elapsed_only=bool(additive and today_running),
                    ),
                )
            )
    results = await asyncio.gather(*(job for _, _, job in jobs), return_exceptions=True)
    values: dict[tuple[str, str, str], float] = {}
    failed: list[str] = []
    ref_days = (reference[1] - reference[0]).days + 1
    cmp_days = (comparison[1] - comparison[0]).days + 1
    for (metric, label, _), result in zip(jobs, results, strict=True):
        if isinstance(result, BaseException) or not getattr(result, "success", False):
            failed.append(f"{metric} ({label})")
            continue
        evidence_ids += result.artifact_ids
        additive = catalogue.aggregation_for(metric) == "additive"
        days = ref_days if label == "ref" else cmp_days
        for payload in _payloads(deps, result.artifact_ids):
            if payload.get("value") is None:
                continue
            key = str(payload["dimensions"].get(entity, "")) if entity else ""
            value = float(payload["value"])
            # Additive totals become per-day figures, so windows of any length compare.
            values[(key, metric, label)] = value / days if additive else value

    mapping_lines = await _mapping(plan, deps, gate, entity, entities, comparison, as_of, today_running, evidence_ids)

    keys = entities if entity else [""]
    table = ["| " + (f"{entity} | " if entity else "") + "metric | reference (per day) | today | change |",
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
    finding_id = _store_finding(deps, evidence_ids, derived, entity)
    basis = (
        "Additive metrics count only the hours elapsed today on every day "
        "(query_metrics elapsed_only) and are shown per day — like for like. Ratios are as "
        "reported: the reference ratio covers full days, today's only the hours so far, so "
        "explain ratio moves from the additive columns, not from the ratio alone."
        if today_running
        else "Additive metrics are shown per day; ratios are as reported."
    )
    head = [
        "[prefetched data — already fetched by the planner's executor; do not re-fetch it]",
        f"Reference window {reference[0]}..{reference[1]} vs {comparison[0]}..{comparison[1]}. {basis}",
    ]
    if entity:
        head.append(f"Entities: the top {len(entities)} {entity} values (planner step 1), in rank order.")
    body = table[: _MAX_TABLE_ROWS + 2]
    tail = []
    if len(table) > _MAX_TABLE_ROWS + 2:
        tail.append(f"(+{len(table) - _MAX_TABLE_ROWS - 2} more rows in finding {finding_id})")
    if mapping_lines:
        tail += ["", *mapping_lines]
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
        supported = set(deps.catalogue.supported_dimensions_for(metric))
        dims: dict[str, Any] = {d: "" for d in breakdown if not supported or d in supported}
        if not dims:
            continue
        if entity:
            dims[entity] = entities
        result = await _query(
            deps, gate, metric_id=metric, dimensions=dims,
            period_start=_at(comparison[0], as_of), period_end=_at(comparison[1], as_of),
            elapsed_only=bool(today_running and deps.catalogue.aggregation_for(metric) == "additive"),
        )
        if not getattr(result, "success", False):
            lines.append(f"- {metric}: not available ({result.summary[:120]})")
            continue
        evidence_ids += result.artifact_ids
        rows = []
        for payload in _payloads(deps, result.artifact_ids):
            labels = ", ".join(f"{k}={v}" for k, v in payload["dimensions"].items() if k != ELAPSED_KEY)
            rows.append(f"  - {labels}: {_fmt(payload.get('value'))}")
        lines.append(f"- {metric} ({len(rows)} rows):")
        lines += rows[:25]
        if len(rows) > 25:
            lines.append(f"  - … {len(rows) - 25} more rows in evidence")
    return lines if len(lines) > 1 else []


def _store_finding(deps: SelericDeps, evidence_ids: list[str], derived: dict[str, float], entity: str | None) -> str | None:
    if not evidence_ids or not derived:
        return None
    finding = Finding(
        finding_type="prefetched_comparison",
        statement=(
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

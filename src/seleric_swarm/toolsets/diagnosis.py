"""DiagnosisToolset — "why did <metric> change?" in one call.

``diagnose_metric_change`` is the composite the agent calls for a why-question.
It plans every candidate from catalogue metadata, fetches through the same
certified Cube path ``query_metrics`` uses (rule 1; budgeted, cached), records
the fetched values as ``EvidenceArtifact``s, and hands the series to the pure
engine in ``causal/diagnosis.py``. It does not call any other tool (rule 4).

Why one composite instead of fetch-then-calculate tool pairs (rule 5): a
diagnosis needs ~two months of daily history for the outcome, its lineage
components and every candidate driver, plus per-segment daily rows for each
dimension — hundreds of evidence rows. Handing those ids across tool calls
through the model is the failure mode the instructions already warn about
(transcribing opaque ids at scale corrupts them). The calculation itself stays
a pure function over the fetched series and is tested on its own.

Candidate planning — nothing is named here:
- **Identities**: the outcome's ``formula.depends_on`` and every ratio whose
  ``depends_on`` includes the outcome (reverse lineage), plus that ratio's
  other components. The engine keeps only those the data verify.
- **Drivers**: additive catalogue metrics that are not computed from the
  outcome, that share a non-scope, non-time dimension with it (a modelled join
  path) or live in its view, ranked by lineage hub score (how many metrics are
  built on them) and by shared dimensions, spread across views.
- **Dimensions**: the outcome's supported dimensions minus time axes and scope
  keys (a dimension carried by nearly every view, e.g. the tenant key), led by
  hierarchy roots and enumerated (low-cardinality) dimensions.
"""

from __future__ import annotations

import asyncio
import functools
import math
import statistics
import time
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import CausalArtifact, EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import DIAGNOSIS_FAILED, DIAGNOSIS_OK, SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.causal import diagnosis as engine
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.exploration import space
from seleric_swarm.services.mcp_query import build_metrics_query_args, dimension_value, row_date
from seleric_swarm.toolsets import policy_config as P
from seleric_swarm.toolsets import semantic

_CALCULATION_VERSION = "diagnosis.v1"
_DEFINITION_BATCH = 10
_DEFINITIONS_TTL_S = 900.0
# Longer than this, an event is compared with the equal-length period before it
# (a period comparison) instead of with "usual" days. Not a limit.
_MAX_EVENT_DAYS = 14
_MAX_SEGMENT_ROWS = 6000
_UNSET_SEGMENT = "(not set)"

# Process-wide cache of full catalogue definitions (lineage lives only there).
_definitions_cache: dict[str, Any] = {"at": 0.0, "ids": frozenset(), "defs": {}}

Direction = Literal["down", "up"]


def _refuse(summary: str, *, error_code: str = "INSUFFICIENT_EVIDENCE", retryable: bool = False) -> ToolResult:
    return ToolResult(success=False, summary=summary, error_code=error_code, retryable=retryable)


# --------------------------------------------------------------------------- metadata
async def _all_definitions(ctx: RunContext[SelericDeps]) -> dict[str, dict[str, Any]]:
    """Every catalogue definition (lineage + aggregation), cached per process."""
    ids = frozenset(ctx.deps.catalogue.metric_ids())
    now = time.monotonic()
    cached = _definitions_cache
    if cached["defs"] and cached["ids"] == ids and now - cached["at"] < _DEFINITIONS_TTL_S:
        return cached["defs"]
    out: dict[str, dict[str, Any]] = {}
    batches = [sorted(ids)[i : i + _DEFINITION_BATCH] for i in range(0, len(ids), _DEFINITION_BATCH)]

    async def one(batch: list[str]) -> None:
        try:
            res = await ctx.deps.mcp_client.call(
                agent_id="v3_agent", capability="seleric.catalogue_get_metrics", arguments={"metric_ids": batch}
            )
        except Exception:
            return
        for mid, d in ((res or {}).get("metrics") or {}).items():
            if isinstance(d, dict):
                out[mid] = d

    await asyncio.gather(*(one(b) for b in batches))
    for mid, d in out.items():
        ctx.deps.query_cache.set(f"metric_definition:{mid}", d)
    if out:
        _definitions_cache.update({"at": now, "ids": ids, "defs": out})
    return out


def _scope_dimensions(ctx: RunContext[SelericDeps]) -> set[str]:
    """Dimensions carried by (nearly) every view — tenant/scope keys, not join paths."""
    return space.scope_dimensions(ctx.deps.catalogue)


def _plan_drivers(
    ctx: RunContext[SelericDeps],
    outcome: str,
    lineage: dict[str, engine.MetricMeta],
    exclude: set[str],
    cap: int,
) -> list[str]:
    cat = ctx.deps.catalogue
    scope = _scope_dimensions(ctx)
    out_dims = {d for d in cat.supported_dimensions_for(outcome) if d not in scope and not cat.is_time_dimension(d)}
    hub = engine.lineage_in_degree(lineage)
    desc = engine.descendants(outcome, lineage)
    # The same measure on another date axis (catalogue ``date_twin``) is the
    # outcome itself, booked differently — never a candidate cause.
    twins = {t for m in (outcome, *exclude) if (t := cat.date_basis_for(m)[1])}
    scope_tokens = frozenset(t for d in scope for t in d.lower().split("_") if t)
    out_units = lineage.get(outcome, engine.MetricMeta(outcome)).entity_tokens(scope_tokens)
    scored: list[tuple[tuple[int, int, str], str, str]] = []
    for m in cat.metrics:
        meta = lineage.get(m.id)
        if meta is None or not meta.additive or m.id in exclude or m.id in desc or m.id == outcome or m.id in twins:
            continue
        if out_units & meta.entity_tokens(scope_tokens):
            continue  # same units as the outcome: a co-measurement, never a cause
        # Every daily series joins the outcome on the date (within the tenant
        # scope); shared dimensions only rank a candidate as more closely modelled.
        shared = {d for d in (m.supported_dimensions or []) if d in out_dims}
        scored.append(((-hub.get(m.id, 0), -len(shared), m.id), m.id, meta.view))
    scored.sort()
    # Round-robin across views so one wide view cannot take every slot.
    by_view: dict[str, list[str]] = {}
    order: list[str] = []
    for _, mid, view in scored:
        if view not in by_view:
            order.append(view)
        by_view.setdefault(view, []).append(mid)
    picked: list[str] = []
    while len(picked) < cap and any(by_view.values()):
        for view in order:
            if by_view[view] and len(picked) < cap:
                picked.append(by_view[view].pop(0))
    return picked


def _plan_dimensions(ctx: RunContext[SelericDeps], outcome: str, cap: int) -> list[str]:
    """The dimensions to segment by: those the question named as where to look first (when the outcome carries
    them), then the catalogue's own plan."""
    planned = space.plan_dimensions(ctx.deps.catalogue, outcome, cap)
    hints = getattr(getattr(ctx.deps, "required_scope", None), "segment_hints", frozenset()) or frozenset()
    if not hints:
        return planned
    carried = set(ctx.deps.catalogue.supported_dimensions_for(outcome))
    first = sorted(d for d in hints if d in carried and not ctx.deps.catalogue.is_time_dimension(d))
    return list(dict.fromkeys([*first, *planned]))[: max(cap, len(first))]


def _rate_parts(
    metric_id: str, lineage: dict[str, engine.MetricMeta], series: dict[str, dict[date, float]], days: list[date],
) -> tuple[str | None, str | None]:
    """The volume a rate is averaged over: its denominator, as the data verify it.

    Lineage lists a ratio's components but not which one divides; weighting the
    segments by the numerator (clicks for a click-through rate) mixes the
    segments on the wrong volume and breaks the mix/rate split. A verified
    ``outcome = a / b`` identity names ``b``; a one-component rate is "per" that
    component. The numerator comes back only from a verified ``a / b``.
    """
    meta = lineage.get(metric_id, engine.MetricMeta(metric_id))
    # Components may live in other views (spend and sales of a return on spend);
    # the engine only trusts a split whose segments rebuild the total on the data.
    additive = {d for d in meta.depends_on if lineage.get(d, engine.MetricMeta(d)).additive}
    for ident in engine.discover_identities(metric_id, lineage, series, days):
        den = [m for m, e in ident.factors if e < 0]
        num = [m for m, e in ident.factors if e > 0]
        if len(ident.factors) == 2 and len(den) == 1 and den[0] in additive:
            return (num[0] if num and num[0] in additive else None), den[0]
    if len(meta.depends_on) == 1 and meta.depends_on[0] in additive:
        return None, meta.depends_on[0]
    return None, None


# --------------------------------------------------------------------------- fetching
def _filters_for(ctx: RunContext[SelericDeps], metric_id: str, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    supported = set(ctx.deps.catalogue.supported_dimensions_for(metric_id))
    family_members = getattr(ctx.deps.catalogue, "family_members", None)
    out, dropped = [], []
    for k, v in (filters or {}).items():
        if v in (None, "", []):
            continue
        if supported and k not in supported:
            # The same slice under the metric's own member of the conformed family (finance_channel = meta is
            # ad_platform / acquisition_platform = meta elsewhere), as query_metrics answers it. Dropped, the
            # driver series of a Meta diagnosis came back for every platform and the scope gate sent the answer
            # back twice (regression 2026-10-09 Q28, 288 s).
            sibling = next(
                (m for m in sorted(family_members(k)) if m in supported), None
            ) if family_members is not None else None
            if sibling is None:
                dropped.append(k)
                continue
            k = sibling
        out.append({"dimension": k, "operator": "equals", "values": list(v) if isinstance(v, list) else [str(v)]})
    return out, dropped


def _supports_brand(ctx: RunContext[SelericDeps], metric_id: str) -> bool:
    from seleric_swarm.services.mcp_query import _BRAND_DIM_KEYS

    cat = ctx.deps.catalogue
    return (not cat.metrics) or bool({d.lower() for d in cat.supported_dimensions_for(metric_id)} & _BRAND_DIM_KEYS)


async def _fetch_daily(
    ctx: RunContext[SelericDeps], metric_id: str, start: date, end: date, filters: dict[str, Any],
    dimension: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    flt, dropped = _filters_for(ctx, metric_id, filters)
    args = build_metrics_query_args(
        measure=metric_id, start=start.isoformat(), end=end.isoformat(), grain="day",
        dimensions=[dimension] if dimension else None, filters=flt or None,
        inject_default_brand=_supports_brand(ctx, metric_id),
    )
    result = await semantic._cached_metrics_query(ctx, args)
    result = dict(result)
    result["_args"] = args
    result["_dropped_filters"] = dropped
    return result, (None if result.get("error") else args)


def _parse_series(result: dict[str, Any], metric_id: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for row in result.get("rows") or []:
        ts, raw = row_date(row), row.get(metric_id)
        if ts is None or raw is None:
            continue
        try:
            out[date.fromisoformat(ts)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def _parse_segments(result: dict[str, Any], metric_id: str, dimension: str) -> dict[str, dict[date, float]]:
    """metric per segment per day. Rows with no value for the dimension are kept as
    one explicit segment: dropping them made a split stop adding up to the total
    (unattributed sales vanished from a sales-per-spend split by campaign)."""
    out: dict[str, dict[date, float]] = {}
    for row in result.get("rows") or []:
        ts, raw = row_date(row), row.get(metric_id)
        seg = dimension_value(row, dimension)
        if ts is None or raw is None:
            continue
        if seg in (None, "", "None", "null"):
            seg = _UNSET_SEGMENT
        try:
            day = date.fromisoformat(ts)
            bucket = out.setdefault(str(seg), {})
            bucket[day] = bucket.get(day, 0.0) + float(raw)
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- artifacts
def _evidence_rows(
    ctx: RunContext[SelericDeps], metric_id: str, values: dict[date, float], days: list[date],
    args: dict[str, Any], index: dict[str, str], tz: Any, dimensions: dict[str, str] | None = None,
    unit: str | None = None,
) -> list[str]:
    ids: list[str] = []
    for d in days:
        if d not in values:
            continue
        start = datetime(d.year, d.month, d.day, tzinfo=tz)
        ev = EvidenceArtifact(
            metric_id=metric_id, dimensions=dict(dimensions or {}), grain="day", as_of=ctx.deps.as_of,
            period_start=start, period_end=start, value=values[d], unit=unit, source_query=args,
        )
        ids.append(semantic._put_evidence(
            ctx, ev, index=index, raw_id=f"raw:{metric_id}:{d}:{d}",
            # The filters the series was fetched with (a channel the question named) are part of what the row
            # means, recorded as query_metrics records them; without them a Meta-filtered diagnosis read as
            # unfiltered and the scope gate looped four times (regression 2026-10-09 Q28).
            provenance=ArtifactProvenance(
                calculation_version=_CALCULATION_VERSION,
                source_metadata={"filters_applied": [f for f in (args.get("filters") or []) if isinstance(f, dict)]},
            ),
        ))
    return ids


def _put_derived(ctx: RunContext[SelericDeps], artifact_type: str, payload: Any, evidence_ids: list[str]) -> str:
    art = ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type=artifact_type,
            payload=payload.model_dump(mode="json"),
            classification="derived",
            evidence_ids=list(evidence_ids),
            provenance=ArtifactProvenance(evidence_ids=list(evidence_ids), calculation_version=_CALCULATION_VERSION),
            mission_id=ctx.deps.mission_id,
        )
    )
    return art.id


_CAUSAL_TIER = {
    "supported_cause": "CAUSALLY_SUPPORTED",
    "likely_contributor": "ASSOCIATION",
    "correlation": "ASSOCIATION",
    "insufficient_evidence": "OBSERVATION",
}


def _finding_metrics(report: engine.DiagnosisReport) -> dict[str, float]:
    """Every number the answer may cite, so the grounding check can trace it."""
    m: dict[str, float] = {}

    def put(key: str, v: Any) -> None:
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            m[key] = float(v)

    e = report.event
    if e is not None:
        for k in ("actual", "reference", "delta", "delta_pct", "expected_by_model", "z_score"):
            put(f"event.{k}", getattr(e, k))
        for k in ("value", "delta", "delta_pct"):
            put(f"previous_period.{k}", e.previous_period.get(k))
    for t in report.decomposition:
        for k in ("event", "reference", "contribution", "share_of_change"):
            put(f"decomposition.{t.metric}.{k}", getattr(t, k))
        if abs(t.reference) > 1e-12:
            put(f"decomposition.{t.metric}.pct_change", t.event / t.reference - 1)
    for t in report.bridge:
        for k in ("event", "reference", "contribution", "share_of_change", "previous", "contribution_vs_previous"):
            put(f"bridge.{t.metric}.{k}", getattr(t, k))
        if abs(t.reference) > 1e-12:
            put(f"bridge.{t.metric}.pct_change", t.event / t.reference - 1)
        if t.previous and abs(t.previous) > 1e-12 and t.contribution_vs_previous is not None:
            put(f"bridge.{t.metric}.pct_change_vs_previous", t.sign * t.contribution_vs_previous / t.previous)
    put("bridge.residual", report.bridge_residual)
    put("bridge.residual_vs_previous", report.bridge_residual_vs_previous)
    for t in report.chain:
        for k in ("event", "reference", "contribution", "share_of_change"):
            put(f"chain.{t.metric}.{k}", getattr(t, k))
        if abs(t.reference) > 1e-12:
            put(f"chain.{t.metric}.pct_change", t.event / t.reference - 1)
    for dim in report.chain_dimensions[:2]:
        put(f"chain_segments.{dim.dimension}.mix_effect", dim.mix_effect)
        put(f"chain_segments.{dim.dimension}.rate_effect", dim.rate_effect)
        for s in dim.top:
            base = f"chain_segments.{dim.dimension}.{s.segment}"
            for k in ("event", "reference", "rate_event", "rate_reference", "z_score"):
                put(f"{base}.{k}", getattr(s, k))
            if s.rate_reference and abs(s.rate_reference) > 1e-12 and s.rate_event is not None:
                put(f"{base}.rate_pct_change", s.rate_event / s.rate_reference - 1)
    for dim in report.dimensions[:3]:
        put(f"segments.{dim.dimension}.mix_effect", dim.mix_effect)
        put(f"segments.{dim.dimension}.rate_effect", dim.rate_effect)
        for s in dim.top:
            base = f"segments.{dim.dimension}.{s.segment}"
            for k in ("event", "reference", "delta", "share_of_change", "rate_event", "rate_reference"):
                put(f"{base}.{k}", getattr(s, k))
            if abs(s.reference) > 1e-12:
                put(f"{base}.pct_change", s.event / s.reference - 1)
    for f in report.drivers:
        base = f"driver.{f.driver}"
        for k in ("effect_per_unit", "p_value", "driver_event", "driver_reference", "driver_delta", "driver_z", "contribution", "share_of_change"):
            put(f"{base}.{k}", getattr(f, k))
        for i, v in enumerate(f.effect_ci):
            put(f"{base}.effect_ci_{i}", v)
        for i, v in enumerate(f.contribution_ci):
            put(f"{base}.contribution_ci_{i}", v)
        if f.driver_reference and abs(f.driver_reference) > 1e-12 and f.driver_event is not None:
            put(f"{base}.pct_change", f.driver_event / f.driver_reference - 1)
    for metric, v in report.previous_values.items():
        put(f"previous_day.{metric}", v)
    for metric, v in report.event_values.items():
        put(f"event_day.{metric}", v)
    put("unexplained_share", report.unexplained_share)
    return m


# --------------------------------------------------------------------------- summary
def _f(v: Any, pct: bool = False) -> str:
    if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return "n/a"
    v = float(v)
    if pct:
        return f"{v * 100:+.1f}%"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    if abs(v) >= 1:
        return f"{v:,.2f}"
    return f"{v:.4g}"


_REFUTER_WORDS = {
    "time_shift_placebo": "placebo with scrambled timing",
    "future_treatment_placebo": "future-value placebo",
    "random_common_cause": "extra random control",
    "data_subset_refuter": "80% data subsets",
    "influential_days_removed": "without the most extreme days",
}
_DIRECTION_WORDS = {
    "temporal": "its past values predict the outcome, not the other way round",
    "intervention": "abrupt on/off shifts in it were followed by the outcome",
    "assumed": "only a same-day association, so direction is not established",
    "feedback": "the outcome's past predicts it, so it may be reacting rather than causing",
}


def _summary(report: engine.DiagnosisReport, lineage: dict[str, engine.MetricMeta]) -> str:
    """What the model reads: the plain-language skeleton plus plain-language checks.

    Internal labels, ids and statistics stay in the artifact (provenance) for the
    API/UI; the model has repeatedly copied them into answers when shown.
    """
    lines = [
        (
            "ANSWER SKELETON — business language; adapt the wording but keep every part and the cause "
            "wording exactly (a cause / a likely contributor / moved together):"
        ),
        *report.narrative,
    ]
    for f in report.drivers:
        if f.status != "implicated" or f.effect_per_unit is None:
            continue
        checks = ", ".join(
            f"{_REFUTER_WORDS.get(r['name'], r['name'].replace('_', ' '))}: {'passed' if r.get('passed') else 'failed'}"
            for r in f.refutations
        )
        lines.append(
            f"CHECKS for {engine._label(f.driver, lineage)}: controls for day-of-week, trend and previous-day values; "
            f"{checks}; direction: {_DIRECTION_WORDS.get(str(f.direction_evidence.get('direction')), 'unknown')}."
            + (f" Moves almost identically with {', '.join(engine._label(x, lineage) for x in f.inseparable_from)}, so"
               " their effects cannot be told apart." if f.inseparable_from else "")
        )
    if report.data_quality:
        lines.append("DATA NOTES: " + " ".join(report.data_quality))
    lines.append(
        "REPORTING RULES: only 'a cause' may be described with caused/drove/because of; arithmetic shares are not "
        "causes; if no cause was identified, say so plainly; every number must come from this result — never "
        "fill a table cell, column or period it does not give; no ids, snake_case labels, z-scores or field names."
    )
    return "\n".join(lines)


# A lever is judged against at least this many of its own normal days, and is out of line beyond this robust z.
_NORMAL_MIN_DAYS = 5
_NORMAL_Z = 2.0


def _named_driver_lines(
    named: list[str],
    series: dict[str, dict[date, float]],
    event_days: list[date],
    reference_days: list[date],
    lineage: dict[str, engine.MetricMeta],
    report: engine.DiagnosisReport,
    history_days: list[date] | None = None,
) -> tuple[str, dict[str, float]]:
    """The drivers to address, each with its move over the event vs the reference days and its test verdict — so
    the answer addresses every hypothesis, and each lever is judged against its OWN normal days: a lever outside
    the range it keeps on ordinary days is where the entity broke, and it is listed first. "Why did the campaign
    not perform yesterday compared to the other days" listed twelve moves against one day, never said which lever
    was out of line, and the answer named nothing (live 2026-10-10 thread_066b9cd1). An average per day keeps
    windows of different lengths comparable; it is a move, not a contribution, unless a verdict says so."""
    verdicts = {f.driver: f for f in report.drivers}
    rows: list[tuple[float, str]] = []
    figures: dict[str, float] = {}
    # The days a lever's normal range is read from: the reference days when there are enough of them, else the
    # last two weeks before the event.
    normal_days = reference_days if len(reference_days) >= _NORMAL_MIN_DAYS else [
        d for d in (history_days or []) if d < min(event_days, default=date.max)
    ][-14:]
    for m in named:
        vals = series.get(m) or {}
        ev = [vals[d] for d in event_days if d in vals]
        rf = [vals[d] for d in reference_days if d in vals]
        label = engine._label(m, lineage)
        if not ev or not rf:
            rows.append((-1.0, f"{label}: no data for this scope over these days, so it could not be checked"))
            continue
        e_avg, r_avg = sum(ev) / len(ev), sum(rf) / len(rf)
        rel = (e_avg / r_avg - 1) if abs(r_avg) > 1e-12 else math.nan
        figures[f"{m} | event average per day"] = e_avg
        figures[f"{m} | reference average per day"] = r_avg
        if math.isfinite(rel):
            figures[f"{m} | change"] = rel
        move = f"{label} {engine._val(r_avg, m, lineage)} → {engine._val(e_avg, m, lineage)} per day ({engine._chg(e_avg, r_avg)})"
        usual = [vals[d] for d in normal_days if d in vals and math.isfinite(vals[d])]
        z = math.nan
        if len(usual) >= _NORMAL_MIN_DAYS:
            med = statistics.median(usual)
            scale = 1.4826 * statistics.median(abs(u - med) for u in usual) or statistics.pstdev(usual)
            z = (e_avg - med) / scale if scale > 1e-12 else math.nan
            span = f"{engine._val(min(usual), m, lineage)}–{engine._val(max(usual), m, lineage)}"
            if math.isfinite(z) and abs(z) >= _NORMAL_Z:
                move += f"; OUTSIDE its own usual range ({span} on {len(usual)} normal days), {'above' if z > 0 else 'below'} it"
            else:
                move += f"; within its own usual range ({span} on {len(usual)} normal days)"
            if math.isfinite(z):
                figures[f"{m} | z vs its own normal days"] = z
        f = verdicts.get(m)
        if f is not None and f.status == "implicated" and f.classification:
            verdict = f"tested: {f.classification.replace('_', ' ')}"
        elif f is not None:
            verdict = f"tested: {f.status.replace('_', ' ')}" + (f" ({f.reason})" if f.reason else "")
        else:
            verdict = ("a rate (or a period comparison), not tested as a cause: its share of the change is in the "
                       "exact split when one is given")
        out_of_range = math.isfinite(z) and abs(z) >= _NORMAL_Z
        rank = (1e6 + abs(z)) if out_of_range else (abs(rel) if math.isfinite(rel) else -1.0)
        rows.append((rank, f"{move} — {verdict}"))
    rows.sort(key=lambda r: -r[0])
    return (
        "DRIVERS TO ADDRESS (every one, in this order — levers OUTSIDE their own usual range first: lead with them "
        "as where it broke, then the size of each move; when none is outside its range, say that no lever broke "
        "from its normal and the day was ordinary variation for this entity; only a tested cause may be called a "
        "cause; never say the user named them): " + "; ".join(text for _, text in rows) + "."
    ), figures


def _outcome_on_its_normal_days(
    metric: str,
    series: dict[str, dict[date, float]],
    event_days: list[date],
    reference_days: list[date],
    history_days: list[date],
    lineage: dict[str, engine.MetricMeta],
) -> str:
    """Where the outcome sits among its own normal days. "Why did the campaign not perform yesterday" had a
    structural answer — net ROAS 0–0.90 on every one of its normal days, never breaking even — and the answer only
    said the day was within normal variation (live 2026-10-10 thread_066b9cd1)."""
    vals = series.get(metric) or {}
    days = reference_days if len(reference_days) >= _NORMAL_MIN_DAYS else [
        d for d in history_days if d < min(event_days, default=date.max)
    ][-14:]
    usual = [vals[d] for d in days if d in vals and math.isfinite(vals[d])]
    ev = [vals[d] for d in event_days if d in vals and math.isfinite(vals[d])]
    if len(usual) < _NORMAL_MIN_DAYS or not ev:
        return ""
    show = lambda v: engine._val(v, metric, lineage)  # noqa: E731
    return (
        f"THE OUTCOME ON ITS OWN NORMAL DAYS: {engine._label(metric, lineage)} ranged {show(min(usual))}–"
        f"{show(max(usual))} (median {show(statistics.median(usual))}) over {len(usual)} normal days; "
        f"{', '.join(str(d) for d in event_days)}: {show(sum(ev) / len(ev))}. Say where the day sits in that range; "
        "when the whole range is poor for this measure (a loss, a return below what was spent), say the entity "
        "under-performs on ordinary days too — a standing problem, not this day's."
    )


def _base_form(
    metric: str, lineage: dict[str, engine.MetricMeta], series: dict[str, dict[date, float]], days: list[date]
) -> tuple[float, dict[str, float]] | None:
    """A metric as constant × Π additive-base^exponent: itself when additive, else its lineage-proposed identity
    the data verify (CPM = 1000 × spend / impressions)."""
    if lineage.get(metric, engine.MetricMeta(metric)).additive:
        return 1.0, {metric: 1.0}
    for ident in engine.discover_identities(metric, lineage, series, days):
        if not ident.approximate and all(lineage.get(f, engine.MetricMeta(f)).additive for f, _ in ident.factors):
            return ident.k, {f: float(e) for f, e in ident.factors}
    return None


def _ratio_split(
    outcome: str,
    named: list[str],
    lineage: dict[str, engine.MetricMeta],
    series: dict[str, dict[date, float]],
    history_days: list[date],
    event_days: list[date],
    reference_days: list[date],
) -> tuple[str, dict[str, float]]:
    """An exact multiplicative split of an outcome (a ratio, or a total the rates rebuild) through the named rates.

    Every metric is written over additive bases (verified identities). The top split uses as few named rates as
    rebuild the outcome with the fewest bases left over; what is left is one more factor named after its bases,
    and among equal splits the one whose leftover joins the closest stages (its ratio nearest 1 on the data: clicks
    per new customer, not impressions per new customer) wins — a funnel read from the data, not from names. Each
    chosen rate is then split exactly by the other named rates (CPC = CPM ÷ CTR). Effects are log changes, so they
    multiply to the outcome's change: CAC = CPC × clicks per new customer, CPC = CPM ÷ CTR — the cost side and the
    conversion side of an acquisition cost, apart."""
    import itertools

    import numpy as np

    out = _base_form(outcome, lineage, series, history_days)
    if out is None:
        return "", {}
    # An additive outcome splits the same way when the named rates rebuild it: gross sales = sessions × conversion
    # rate × order value. Left out, every rate behind a sales or profit drop was "described, not tested" and the
    # answer named no driver while conversion had halved on flat traffic (live 2026-10-10 thread_066b9cd1). A split
    # that leaves the outcome itself in its leftover factor explains nothing and is never taken.
    additive_outcome = lineage.get(outcome, engine.MetricMeta(outcome)).additive
    forms = {m: f for m in named if m != outcome and (f := _base_form(m, lineage, series, history_days)) is not None
             and not lineage.get(m, engine.MetricMeta(m)).additive}
    if not forms:
        return "", {}
    bases = sorted({b for _, v in [out, *forms.values()] for b in v})
    vec = lambda v: np.array([v.get(b, 0.0) for b in bases])  # noqa: E731

    def avg(metric: str, days: list[date]) -> float | None:
        vals = [v for d in days if (v := series.get(metric, {}).get(d)) is not None]
        return sum(vals) / len(vals) if vals else None

    def value(k: float, form: dict[str, float], days: list[date]) -> float | None:
        v = k
        for b, e in form.items():
            a = avg(b, days)
            if a is None or a <= 0:
                return None
            v *= a**e
        return v

    def split(
        target: np.ndarray, cands: list[str], exact: bool, deepest: bool = False
    ) -> tuple[dict[str, int], dict[str, float]] | None:
        best: tuple[tuple[float, ...], dict[str, int], dict[str, float]] | None = None
        for signs in itertools.product((-1, 0, 1), repeat=len(cands)):
            chosen = {m: sg for m, sg in zip(cands, signs, strict=True) if sg}
            if not chosen or np.linalg.matrix_rank(np.array([vec(forms[m][1]) for m in chosen])) < len(chosen):
                continue
            residual = target - sum(sg * vec(forms[m][1]) for m, sg in chosen.items())
            res = {b: float(e) for b, e in zip(bases, residual, strict=True) if abs(e) > 1e-9}
            if exact and res:
                continue
            if additive_outcome and outcome in res:
                continue
            rv = value(1.0, res, reference_days) if res else 1.0
            closeness = abs(math.log(rv)) if rv else math.inf
            # A total reads deepest: sessions × conversion × order value, not orders × order value (which only
            # restates the drop); a ratio reads through the fewest rates.
            score = (len(res), -len(chosen) if deepest else len(chosen), closeness)
            if best is None or score < best[0]:
                best = (score, chosen, res)
        return None if best is None else (best[1], best[2])

    cands = list(forms)[:6]
    top = split(vec(out[1]), cands, exact=False, deepest=additive_outcome)
    if top is None:
        return "", {}
    chosen, res_form = top
    # A leftover of more than one stage ("contribution margin × impressions per ad spend × clicks") names no lever
    # anyone can act on: no split rather than an unreadable one (live 2026-10-10 thread_066b9cd1).
    if len(res_form) > 2:
        return "", {}

    def res_label(form: dict[str, float]) -> str:
        num = " × ".join(engine._label(b, lineage) for b, e in form.items() if e > 0) or "1"
        den = " × ".join(engine._label(b, lineage) for b, e in form.items() if e < 0)
        return f"{num} per {den}" if den else num

    o_ev, o_rf = value(out[0], out[1], event_days), value(out[0], out[1], reference_days)
    if not o_ev or not o_rf:
        return "", {}
    figures: dict[str, float] = {}

    def factor_text(m: str, label: str, k: float, form: dict[str, float], sign: int, of: str) -> tuple[float, str] | None:
        ev, rf = value(k, form, event_days), value(k, form, reference_days)
        if not ev or not rf:
            return None
        eff = sign * math.log(ev / rf)
        shown = (lambda v: engine._val(v, m, lineage)) if m in lineage else (lambda v: f"{v:,.4g}")
        figures[f"{label} | event"], figures[f"{label} | reference"] = ev, rf
        figures[f"{label} | effect on {of} %"] = math.exp(eff) * 100 - 100
        return eff, (f"{label} {shown(rf)} → {shown(ev)} ({engine._chg(ev, rf)}), moving {engine._label(of, lineage)} "
                     f"{math.exp(eff) * 100 - 100:+.1f}%")

    rows: list[tuple[float, str]] = []
    nested: list[str] = []
    for m, sg in chosen.items():
        got = factor_text(m, engine._label(m, lineage), forms[m][0], forms[m][1], sg, outcome)
        if got is None:
            return "", {}
        rows.append(got)
        others = [c for c in cands if c not in chosen]
        if others and (sub := split(vec(forms[m][1]), others, exact=True)) is not None:
            parts = [factor_text(c, engine._label(c, lineage), forms[c][0], forms[c][1], s2, m) for c, s2 in sub[0].items()]
            if all(parts):
                nested.append(
                    f"{engine._label(m, lineage)} itself = "
                    + " ".join(("× " if s2 > 0 else "÷ ") + engine._label(c, lineage) for c, s2 in sub[0].items()).lstrip("× ")
                    + ": " + "; ".join(t for _, t in sorted(parts, key=lambda p: -abs(p[0])))  # type: ignore[index]
                )
    if res_form:
        got = factor_text("residual", res_label(res_form), 1.0, res_form, 1, outcome)
        if got is None:
            return "", {}
        rows.append(got)
    rows.sort(key=lambda r: -abs(r[0]))
    formula = " ".join(("× " if sg > 0 else "÷ ") + engine._label(m, lineage) for m, sg in chosen.items()).lstrip("× ")
    if res_form:
        formula += f" × {res_label(res_form)}"
    total = math.log(o_ev / o_rf)
    figures[f"{outcome} | change %"] = math.exp(total) * 100 - 100
    return (
        f"EXACT SPLIT OF THE CHANGE — answer 'which driver / rank the drivers' from this, in this order, each with "
        f"its effect (a multiplicative identity of the catalogue's definitions, verified on the data; the effects "
        f"multiply to the total; arithmetic, not a causal claim): {engine._label(outcome, lineage)} = "
        f"{formula} (× a constant). " + "; ".join(t for _, t in rows)
        + f"; together {engine._label(outcome, lineage)} {math.exp(total) * 100 - 100:+.1f}%."
        + (" " + " ".join(n + "." for n in nested) if nested else "")
    ), figures


def _scope_line(filters: dict[str, Any]) -> str:
    parts = [
        f"{k.replace('_', ' ')} = {', '.join(str(x) for x in (v if isinstance(v, list) else [v]))}"
        for k, v in (filters or {}).items() if v not in (None, "", [])
    ]
    return ("SCOPE OF THIS DIAGNOSIS — every figure below is for " + "; ".join(parts) + " alone, not for any other entity.") if parts else ""


def _funnel_pool(ctx: RunContext[SelericDeps], metric_id: str, lineage: dict[str, engine.MetricMeta]) -> list[str]:
    """The countable events that close a ratio outcome's funnel: the counts some catalogue rate divides one component
    by, that are counted in the other component's own view (cost per order divides spend by orders, and orders sit
    in the view of the contribution margin they carry). The ladder's last stage is read from these."""
    out_meta = lineage.get(metric_id, engine.MetricMeta(metric_id))
    if out_meta.additive:
        return []
    cat = ctx.deps.catalogue
    comps = [d for d in out_meta.depends_on if d in lineage and lineage[d].additive]
    pool: list[str] = []
    for base in comps:
        views = {lineage[o].view for o in comps if o != base}
        for r in lineage.values():
            if r.additive or base not in r.depends_on:
                continue
            for d in r.depends_on:
                meta = lineage.get(d)
                if (meta and meta.additive and meta.unit == "count" and d not in comps and meta.view in views
                        and (cat.has_metric(d) or not cat.metrics) and _supports_brand(ctx, d)):
                    pool.append(d)
    return list(dict.fromkeys(pool))


def _funnel_ladder(
    outcome: str,
    ends: list[str],
    stages_named: list[str],
    lineage: dict[str, engine.MetricMeta],
    series: dict[str, dict[date, float]],
    history_days: list[date],
    event_days: list[date],
    reference_days: list[date],
) -> tuple[str, dict[str, float], list[str]]:
    """The stages between a ratio's denominator and its numerator, each with its exact effect.

    outcome = N / D telescopes through the counts between them: N / D = (s1 / D) x (s2 / s1) x ... x (N / sk). The
    product is the outcome whatever the stages are, so the effects (log changes of each stage ratio) multiply to
    the outcome's change; the stages only decide how readable the split is. The last stage is the count counted in
    the numerator's own view (contribution margin per order, not per new customer); the stages before it are the
    counts the question or the diagnosis named (through the rates they named), ordered by volume, since a funnel
    narrows. With none named it is two stages: orders per unit of spend, and the numerator per order — what an
    order cost and what it was worth. Arithmetic, not a causal claim."""
    ident = next((
        i for i in engine.discover_identities(outcome, lineage, series, history_days)
        if not i.approximate and sorted(e for _, e in i.factors) == [-1.0, 1.0]
        and all(lineage.get(m, engine.MetricMeta(m)).additive for m, _ in i.factors)
    ), None)
    if ident is None or not event_days or not reference_days:
        return "", {}, []
    num = next(m for m, e in ident.factors if e > 0)
    den = next(m for m, e in ident.factors if e < 0)

    def total(m: str, days: list[date]) -> float | None:
        vals = [series[m][d] for d in days if d in series.get(m, {})]
        return sum(vals) if vals else None

    def usable(m: str) -> bool:
        return all((t := total(m, w)) is not None and t > 0 for w in (event_days, reference_days))

    if not usable(num) or not usable(den):
        return "", {}, []
    num_view = lineage[num].view
    closing = [m for m in ends if m in series and usable(m) and lineage[m].view == num_view]
    if not closing:
        return "", {}, []

    def volume(m: str) -> float:
        return (total(m, event_days) or 0.0) / len(event_days) + (total(m, reference_days) or 0.0) / len(reference_days)

    last = max(closing, key=volume)
    middle = sorted(
        (m for m in dict.fromkeys(stages_named)
         if m in series and m not in (num, den, last) and usable(m) and lineage.get(m, engine.MetricMeta(m)).additive
         and lineage[m].unit == "count" and lineage[m].view != num_view and volume(m) >= volume(last)),
        key=lambda m: -volume(m),
    )
    shown = lambda m: engine._label(m, lineage)  # noqa: E731

    def ratio(a: str, b: str, days: list[date]) -> float | None:
        shared = [d for d in days if d in series.get(a, {}) and d in series.get(b, {})]
        top, bottom = (sum(series[a][d] for d in shared), sum(series[b][d] for d in shared)) if shared else (0.0, 0.0)
        return top / bottom if bottom > 0 and top > 0 else None

    def narrows(lo: str, hi: str) -> bool:
        return all((r := ratio(hi, lo, w)) is not None and r <= 1.0 for w in (event_days, reference_days))

    # A funnel narrows: a stage that is larger than the one before it (link clicks beside sessions) is another
    # system's count of the same traffic, and a count within a tenth of the closing one is that event counted twice
    # (orders in the sales ledger and in the P&L); neither is a step.
    chain: list[str] = []
    for m in middle:
        if abs(math.log(volume(m) / volume(last))) < 0.1:
            continue
        if narrows(chain[-1] if chain else m, m) if chain else True:
            chain.append(m)
    seq = [den, *chain, last, num]

    rows: list[tuple[float, str]] = []
    figures: dict[str, float] = {}
    names: list[str] = []
    cum = 0.0
    for lo, hi in zip(seq, seq[1:], strict=False):
        ev, rf = ratio(hi, lo, event_days), ratio(hi, lo, reference_days)
        if ev is None or rf is None:
            return "", {}, []
        label = f"{shown(hi)} per {shown(lo)}"
        eff = math.log(ev / rf)
        cum += eff
        both_counts = lineage[lo].unit == "count" and lineage[hi].unit == "count"
        show = (lambda v: f"{v:.2%}") if both_counts and ev <= 1 and rf <= 1 else (lambda v: f"{v:,.4g}")
        unit = "INR " if lineage[hi].unit not in ("count", "ratio") and lineage[lo].unit == "count" else ""
        shown_ev, shown_rf, shown_label = ev, rf, label
        if lo == den and lineage[lo].unit not in ("count", "ratio") and lineage[hi].unit == "count":
            # The first stage as the cost it is: ad spend per order, not orders per unit of spend.
            shown_ev, shown_rf, shown_label, unit = 1 / ev, 1 / rf, f"{shown(lo)} per {shown(hi)}", "INR "
            if shown_rf < 1 and shown_ev < 1:
                # a cost under one unit per event reads as a cost per thousand (CPM), not as a fraction of a rupee
                shown_ev, shown_rf, shown_label = shown_ev * 1000, shown_rf * 1000, f"{shown(lo)} per 1,000 {shown(hi)}"
            names.append(f"1 ÷ {shown_label}")
        else:
            names.append(label)
        figures[f"{shown_label} | event"], figures[f"{shown_label} | reference"] = shown_ev, shown_rf
        figures[f"{shown_label} | effect on {outcome} %"] = math.exp(eff) * 100 - 100
        rows.append((eff, f"{shown_label} {unit}{show(shown_rf)} → {unit}{show(shown_ev)} ({engine._chg(shown_ev, shown_rf)}), "
                          f"moving {shown(outcome)} {math.exp(eff) * 100 - 100:+.1f}%"))
    figures[f"{outcome} | lever ladder change %"] = math.exp(cum) * 100 - 100
    rows.sort(key=lambda r: -abs(r[0]))
    return (
        "EXACT LEVER LADDER — answer 'what are the levers / which one moved it' from this, largest first. "
        f"{shown(outcome)} = " + " × ".join(names) + " (consecutive stages of the funnel; the effects multiply to the "
        "total; arithmetic, not a causal claim): " + "; ".join(t for _, t in rows)
        + f"; together {shown(outcome)} {math.exp(cum) * 100 - 100:+.1f}%."
    ), figures, list(seq)


def _change_log_metrics(ctx: RunContext[SelericDeps], lineage: dict[str, engine.MetricMeta]) -> list[str]:
    """The catalogue's change-event counts (budget, status, bid-strategy edits): additive counts whose grain is
    a change event. Found by grain, not by name."""
    cat = ctx.deps.catalogue
    return [
        m for m, meta in lineage.items()
        if meta.additive and meta.unit == "count" and "change" in meta.entity_tokens()
        and (cat.has_metric(m) or not cat.metrics)
    ]


def _change_log_lines(
    metrics: list[str],
    series: dict[str, dict[date, float]],
    event_days: list[date],
    reference_days: list[date],
    lineage: dict[str, engine.MetricMeta],
) -> tuple[str, dict[str, float]]:
    """What was edited on the ads in scope: the change log's counts on the event days against the usual days.
    The log records that something changed, not its old and new value."""
    parts: list[str] = []
    figures: dict[str, float] = {}
    for m in metrics:
        vals = series.get(m) or {}
        ev = sum(vals.get(d, 0.0) for d in event_days) / len(event_days)
        rf = sum(vals.get(d, 0.0) for d in reference_days) / max(len(reference_days), 1)
        if ev <= 0 and rf <= 0:
            continue
        figures[f"{m} | event average per day"], figures[f"{m} | reference average per day"] = ev, rf
        parts.append(f"{engine._label(m, lineage)}: {rf:.1f} → {ev:.1f} per day")
    if not parts:
        return "", {}
    return (
        "CHANGES LOGGED ON THE ADS IN SCOPE (what the account edited, as counts of logged edits per day — the log "
        "does not hold what each edit changed, so state which kinds of edit were logged and on which days, never "
        "that one caused the move): " + "; ".join(parts) + "."
    ), figures


def _inclusive_days(start_dt: datetime, end_dt: datetime) -> tuple[date, date]:
    """First and last day of a window. The end day is included, as in ``query_metrics``
    and the resolved-window pin: reading a midnight end as exclusive dropped the last
    day of every pinned multi-day window — "last week" (09-28..10-04) was diagnosed as
    09-28..10-03, and the scope gate then sent the answer back for the missing day
    (live 2026-10-09 MS3-2ec0e4a115)."""
    start, end = start_dt.date(), end_dt.date()
    return (start, end) if start <= end else (end, start)


# --------------------------------------------------------------------------- the tool
async def _diagnose(
    ctx: RunContext[SelericDeps],
    metric_id: str,
    event_start: datetime | None = None,
    event_end: datetime | None = None,
    claimed_direction: Direction | None = None,
    filters: dict[str, str | list[str]] | None = None,
    search_breadth: Literal[0, 1, 2] = 0,
    compare_start: datetime | None = None,
    compare_end: datetime | None = None,
    drivers: list[str] | None = None,
) -> ToolResult:
    """Explain why a metric changed: event size, what/where it changed, and evidence-ranked causes.

    Use for any "why did X rise/fall/change" question after resolving X to a
    catalogue metric id. ``event_start``/``event_end`` default to the
    question's period (else yesterday). ``claimed_direction`` is the direction
    the user asserts ("down" for fell/dropped, "up" for rose/increased) — the
    tool checks it against the data. ``filters`` scopes the outcome (e.g. one
    channel). Returns the event magnitude vs a same-weekday reference, an exact
    decomposition, segment localisation (incl. Simpson's paradox), and upstream
    drivers estimated with DoWhy + refuters, each classified supported_cause /
    likely_contributor / correlation / insufficient_evidence. Report those
    labels faithfully; do not upgrade them.

    Period comparisons ("this month vs last month", "vs the previous period"):
    pass ``compare_start``/``compare_end`` for the period compared with (same
    length as the event window). Any length works; a window longer than two weeks is always
    compared with the equal-length period just before it. The result then
    splits the difference between the two periods exactly by component and by
    segment; upstream drivers are not estimated for period comparisons.

    ``drivers``: catalogue metric ids the user names as possible explanations ("check whether it came from CPM,
    CTR, checkout conversion …" — resolve each to its id first). Each one is fetched and reported with how it
    moved over the same days, and tested as a cause where it can be; report every one of them.

    For a ratio outcome (ROAS, CAC, a cost or a rate) the result adds an exact LEVER LADDER: the outcome as a product
    of the funnel stages between its parts, each stage's effect on it. Name the stages you want in the ladder as
    ``drivers`` — the rates of the funnel (cost per mille, click-through, conversion, the cost per click) from the
    catalogue; without any it shows what an order cost and what an order was worth. When ``filters`` scope the
    outcome to particular campaigns or ads, the result also lists the edits logged on them (budget, status, bid
    strategy) on the event days against the usual days.
    """
    if (unknown := await semantic._reject_unknown_metric(ctx, metric_id)) is not None:
        return unknown
    tz = ctx.deps.as_of.tzinfo
    today = ctx.deps.as_of.date()
    notes: list[str] = []
    window = ctx.deps.resolved_window
    if event_start is None and event_end is None and window is not None and window.start and window.end:
        ev_start, ev_end = date.fromisoformat(window.start), date.fromisoformat(window.end)
    else:
        default = ctx.deps.as_of - timedelta(days=1)
        start_dt, end_dt = event_start or event_end or default, event_end or event_start or default
        # The question named a period: hold the call to it (same rule as query_metrics) — unless that would put the
        # event on top of the days it is compared with. "Last 3 days … why worse yesterday" names a data window and an
        # event inside it; pinning yesterday to the whole window made it overlap its own baseline and the diagnosis was
        # refused twice (live 2026-10-10 thread_d1844eb0). A pin that leaves no valid call is not a correction.
        if (pinned := semantic._pin_to_resolved_window(ctx, start_dt, end_dt)) is not None:
            p_start, p_end, note = pinned
            against = compare_start or compare_end
            if against is not None:
                c_first, c_last = _inclusive_days(against, compare_end or compare_start)  # type: ignore[arg-type]
                p_first, p_last = _inclusive_days(p_start, p_end)
                if p_first <= c_last and c_first <= p_last:
                    pinned = None
            if pinned is not None:
                start_dt, end_dt = p_start, p_end
                notes.append(note)
        ev_start, ev_end = _inclusive_days(start_dt, end_dt)
    if ev_end < ev_start:
        ev_start, ev_end = ev_end, ev_start
    n_event = (ev_end - ev_start).days + 1
    event_days = [ev_start + timedelta(days=i) for i in range(n_event)]
    # Days still in progress cannot be compared with complete ones. Drop them
    # when complete days remain; the engine refuses a window that is all partial.
    running_left_out = 0
    if (complete := [d for d in event_days if d < today]) and len(complete) < len(event_days):
        notes.append(f"{', '.join(str(d) for d in event_days if d >= today)} is still in progress and was left out")
        running_left_out = len(event_days) - len(complete)
        event_days = complete
        ev_start, ev_end = event_days[0], event_days[-1]
    partial = {d for d in event_days if d >= today}
    # Period comparison: an explicit comparison window, or the equal-length
    # period just before a window too long to compare with "usual" days.
    baseline: list[date] = []
    # Every day of the comparison period, kept for the levers: per-day averages need no equal length, and trimming
    # "the other days" to the one before compared yesterday's levers with one day only (live 2026-10-10).
    full_baseline: list[date] = []
    if compare_start is not None or compare_end is not None:
        c_start, c_end = _inclusive_days(compare_start or compare_end, compare_end or compare_start)  # type: ignore[arg-type]
        c_days = [c_start + timedelta(days=i) for i in range((c_end - c_start).days + 1)]
        full_baseline = list(c_days)
        if running_left_out and len(c_days) == len(event_days) + running_left_out:
            # A period to date against its counterpart: the running day left the event, so its
            # counterpart (the comparison's last day) leaves too — the same span from the start
            # (live 2026-10-09: 10-05..10-08 was compared with 09-29..10-02, not 09-28..10-01).
            c_days = c_days[: len(event_days)]
            notes.append(f"compared with {c_days[0]}..{c_days[-1]}, the same days of the comparison period")
        if len(c_days) > len(event_days) and len(event_days) <= _MAX_EVENT_DAYS:
            # "Yesterday compared to the other days": a short event against several days is read against its usual
            # days (the reference with a z), not against the last of them; the levers are compared with every day
            # of the period (full_baseline). Trimmed to the day before, the headline compared one day with one.
            notes.append(
                f"{', '.join(str(d) for d in event_days)} is compared with its usual days and its levers with each "
                f"day of {c_start}..{c_end}"
            )
            c_days = []
        elif len(c_days) != len(event_days):
            notes.append(
                f"the comparison period {c_start}..{c_end} has {len(c_days)} days and the period asked about "
                f"{len(event_days)}; compared with the {len(event_days)} days ending {c_end} so totals are like for like"
            )
            c_days = [c_end - timedelta(days=i) for i in range(len(event_days) - 1, -1, -1)]
        baseline = c_days
    elif len(event_days) > _MAX_EVENT_DAYS:
        baseline = [d - timedelta(days=len(event_days)) for d in event_days]
    compared = [*baseline, *full_baseline]
    if compared and max(compared) >= ev_start:
        return _refuse(
            f"the comparison period must end before the event starts ({ev_start}..{ev_end}); it was "
            f"{min(compared)}..{max(compared)}. Pass compare_start/compare_end "
            f"days before {ev_start}, or event_start/event_end for the day to explain",
            error_code="UNSUPPORTED_QUERY",
        )
    hist_start = ev_start - timedelta(days=P.DIAG_HISTORY_DAYS)
    if baseline or full_baseline:
        hist_start = min(hist_start, min([*baseline[:1], *full_baseline[:1]]) - timedelta(days=_MAX_EVENT_DAYS))
    filters = dict(filters or {})
    if pooled := [k for k, v in filters.items() if isinstance(v, list) and len({str(x) for x in v}) > 1]:
        # One series for several entities: whether each of them moved is not visible in it.
        notes.append(
            f"the {len(filters[pooled[0]])} values of {pooled[0]} are pooled into one series here; call once per "
            "entity to see which of them moved and whether each moved at all"
        )

    definitions = await _all_definitions(ctx)
    if metric_id not in definitions:
        return _refuse(f"no catalogue definition for {metric_id}; cannot read its lineage")
    lineage = engine.lineage_from_definitions(definitions)
    out_meta = lineage[metric_id]

    # ---- plan ---------------------------------------------------------------------------
    identity_metrics: list[str] = [d for d in out_meta.depends_on if d in lineage and d != metric_id]
    for r in lineage.values():
        if metric_id in r.depends_on and r.id != metric_id:
            identity_metrics.append(r.id)
            identity_metrics += [d for d in r.depends_on if d not in (metric_id, r.id)]
    identity_metrics = list(dict.fromkeys(m for m in identity_metrics if ctx.deps.catalogue.has_metric(m) or not ctx.deps.catalogue.metrics))
    # Next-link candidates: "rate per base" ratios that could rebuild the outcome
    # or one of its additive components (lineage-proposed; the data decide).
    chain_targets = [metric_id] if out_meta.additive else []
    chain_targets += [m for m in identity_metrics if lineage.get(m, engine.MetricMeta(m)).additive]
    chain_metrics: list[str] = []
    for t in chain_targets:
        for rate, base in engine.rate_chain_proposals(t, lineage):
            chain_metrics += [rate, base]
    chain_metrics = [m for m in dict.fromkeys(chain_metrics) if m not in identity_metrics and m != metric_id]
    cap = P.DIAG_MAX_DRIVERS + 2 * int(search_breadth)
    named = [d for d in dict.fromkeys(str(x).strip() for x in (drivers or [])) if d and d != metric_id]
    if ctx.deps.catalogue.metrics:
        if unknown_named := [d for d in named if not ctx.deps.catalogue.has_metric(d)]:
            notes.append(f"not catalogue metric ids, so not checked: {', '.join(unknown_named)}")
        named = [d for d in named if d not in unknown_named]
    # A named driver is always tested when it is a quantity the engine can estimate (additive); a named rate is
    # still fetched and its move reported over the same days.
    drivers = list(dict.fromkeys([
        *(d for d in named if lineage.get(d, engine.MetricMeta(d)).additive and d not in identity_metrics),
        *_plan_drivers(ctx, metric_id, lineage, set(identity_metrics), cap),
    ]))
    dims = _plan_dimensions(ctx, metric_id, P.DIAG_MAX_DIMENSIONS + 4 * int(search_breadth))

    # ---- fetch ---------------------------------------------------------------------------
    sem = asyncio.Semaphore(6)

    async def guarded(coro: Any) -> Any:
        async with sem:
            return await coro

    # Accounting-bridge candidates: every additive metric in the outcome's unit
    # (only quantities of one unit can sum to it). The engine keeps a signed sum
    # only if it reproduces the outcome on every history day.
    bridge_pool: list[str] = []
    if out_meta.additive and out_meta.unit:
        bridge_pool = [
            m for m, meta in lineage.items()
            if m != metric_id and meta.additive and meta.unit == out_meta.unit
            and ctx.deps.catalogue.has_metric(m) and _supports_brand(ctx, m)
        ]
    named_parts = [d for n in named for d in lineage.get(n, engine.MetricMeta(n)).depends_on if d in lineage]
    # A ratio outcome is also read through the funnel between its denominator and numerator, and an outcome scoped to
    # particular entities through what the account edited on them (budget / status / bid edits).
    ladder_ends = _funnel_pool(ctx, metric_id, lineage)
    change_log = _change_log_metrics(ctx, lineage) if filters else []
    # The count of the outcome's own units, so a driver counting the same events is recognised as such.
    unit_count = engine.unit_count_metric(metric_id, lineage)
    unit_counts = [unit_count] if unit_count and ctx.deps.catalogue.has_metric(unit_count) else []
    series_ids = list(dict.fromkeys(
        [metric_id, *identity_metrics, *chain_metrics, *drivers, *named, *named_parts, *bridge_pool,
         *ladder_ends, *change_log, *unit_counts]
    ))
    series_results = await asyncio.gather(*(guarded(_fetch_daily(ctx, m, hist_start, ev_end, filters)) for m in series_ids))

    quality: list[str] = []
    series: dict[str, dict[date, float]] = {}
    args_by_metric: dict[str, dict[str, Any]] = {}
    # The currency the data source reports per metric (as query_metrics records it),
    # so diagnosis evidence carries its unit like every other figure.
    units: dict[str, str] = {}
    for m, (res, args) in zip(series_ids, series_results, strict=True):
        if args is None:
            if m == metric_id:
                return semantic._fetch_failure(f"diagnose_metric_change({metric_id})", res.get("error"))
            quality.append(f"{m}: fetch failed ({str(res.get('error'))[:120]})")
            continue
        if res.get("_dropped_filters"):
            if m in change_log or (m in ladder_ends and m not in bridge_pool):
                # Not recorded for this scope: a stage or an edit count for the whole account describes another scope.
                quality.append(f"{m} is not recorded by {', '.join(res['_dropped_filters'])}, so it is left out of this scope")
                continue
            if m in bridge_pool and m not in (metric_id, *identity_metrics, *chain_metrics, *drivers, *named, *named_parts):
                # An unfiltered total cannot be a term of a filtered outcome.
                bridge_pool.remove(m)
                continue
            if m != metric_id:
                # A whole-account figure beside a filtered outcome describes another scope: the account's web
                # purchases were reported as one campaign's (live 2026-10-10 thread_066b9cd1).
                quality.append(
                    f"{m} is not recorded by {', '.join(res['_dropped_filters'])}, so it is left out of this scope"
                )
                continue
            quality.append(f"{m} cannot be filtered by {', '.join(res['_dropped_filters'])}; used unfiltered")
        parsed = _parse_series(res, m)
        if parsed:
            series[m] = parsed
            args_by_metric[m] = args
            if currency := str((res.get("provenance") or {}).get("currency") or "").strip():
                units[m] = currency
    if metric_id not in series:
        return _refuse(f"no daily data for {metric_id} over {hist_start}..{ev_end}")
    history_days = sorted(d for d in series[metric_id] if d < ev_start and d not in partial)
    numerator, weight = (None, None) if out_meta.additive else _rate_parts(metric_id, lineage, series, history_days)
    seg_jobs = [(metric_id, d) for d in dims] + [(m, d) for m in (weight, numerator) if m for d in dims]
    seg_results = await asyncio.gather(*(guarded(_fetch_daily(ctx, m, hist_start, ev_end, filters, dimension=d)) for m, d in seg_jobs))
    segments: dict[str, dict[str, dict[str, dict[date, float]]]] = {}
    seg_args: dict[tuple[str, str], dict[str, Any]] = {}
    for (m, d), (res, args) in zip(seg_jobs, seg_results, strict=True):
        if args is None or len(res.get("rows") or []) > _MAX_SEGMENT_ROWS:
            continue
        parsed = _parse_segments(res, m, d)
        if len(parsed) >= 2:
            segments.setdefault(m, {})[d] = parsed
            seg_args[(m, d)] = args

    # Phase B: segments for every chain rate the data actually verify (the
    # engine picks which component leads; fetching for each verified chain
    # avoids re-implementing that choice here).
    targets = [metric_id] if out_meta.additive else []
    for ident in engine.discover_identities(metric_id, lineage, series, history_days)[:1]:
        targets += [m for m, _ in ident.factors if lineage.get(m, engine.MetricMeta(m)).additive]
    chain_pairs: list[tuple[str, str]] = []
    for t in dict.fromkeys(targets):
        for ident in engine.discover_rate_chains(t, lineage, series, history_days)[:1]:
            chain_pairs.append((ident.factors[0][0], ident.factors[1][0]))
    jobs_b: list[tuple[str, str]] = []
    for rate_m, base_m in dict.fromkeys(chain_pairs):
        base_dims = set(ctx.deps.catalogue.supported_dimensions_for(base_m))
        for d in _plan_dimensions(ctx, rate_m, P.DIAG_MAX_DIMENSIONS // 2):
            if d in base_dims:
                jobs_b += [(rate_m, d), (base_m, d)]
    jobs_b = [j for j in dict.fromkeys(jobs_b) if j not in seg_args]
    res_b = await asyncio.gather(*(guarded(_fetch_daily(ctx, m, hist_start, ev_end, filters, dimension=d)) for m, d in jobs_b))
    for (m, d), (res, args) in zip(jobs_b, res_b, strict=True):
        if args is None or len(res.get("rows") or []) > _MAX_SEGMENT_ROWS:
            continue
        parsed = _parse_segments(res, m, d)
        if len(parsed) >= 2:
            segments.setdefault(m, {})[d] = parsed
            seg_args[(m, d)] = args

    inp = engine.DiagnosisInput(
        outcome=metric_id, event_days=event_days, series=series, lineage=lineage, segments=segments,
        candidate_drivers=[d for d in drivers if d in series], partial_days=partial,
        claimed_direction=claimed_direction, denominators=({metric_id: weight} if weight else {}),
        numerators=({metric_id: numerator} if numerator and weight else {}),
        scope_tokens=frozenset(t for d in _scope_dimensions(ctx) for t in d.lower().split("_") if t),
        baseline_days=baseline,
        bridge_candidates=[m for m in bridge_pool if m in series],
    )
    for _ in inp.candidate_drivers:
        if not ctx.deps.budget.consume("causal_queries").ok:
            inp.candidate_drivers = inp.candidate_drivers[: max(0, len(inp.candidate_drivers) - 1)]
    report = await asyncio.to_thread(engine.diagnose, inp)
    report.data_quality = [*notes, *quality, *report.data_quality]

    # ---- evidence + derived artifacts ------------------------------------------------
    index = semantic._evidence_index(ctx)
    cited_days = sorted({*event_days, *(date.fromisoformat(r) for v in (report.event.reference_days.values() if report.event else []) for r in v)})
    if report.event and report.event.previous_period.get("days"):
        cited_days = sorted({*cited_days, *(date.fromisoformat(x) for x in report.event.previous_period["days"])})
    evidence_ids: list[str] = []
    per_metric_ids: dict[str, list[str]] = {}
    cited_metrics = [metric_id, *(t.metric for t in report.decomposition), *(t.metric for t in report.bridge),
                     *(t.metric for t in report.chain),
                     *(f.driver for f in report.drivers if f.status in ("implicated", "ruled_out") and f.driver in series)]
    for m in dict.fromkeys(cited_metrics):
        if m in series:
            ids = _evidence_rows(ctx, m, series[m], cited_days, args_by_metric[m], index, tz, unit=units.get(m))
            per_metric_ids[m] = ids
            evidence_ids += ids
    chain_pair = tuple(t.metric for t in report.chain)
    for dim in report.chain_dimensions[:2]:
        for s in dim.top[:3]:
            for m in chain_pair:
                if (m, dim.dimension) in seg_args:
                    evidence_ids += _evidence_rows(
                        ctx, m, segments[m][dim.dimension].get(s.segment, {}), cited_days, seg_args[(m, dim.dimension)],
                        index, tz, {dim.dimension: s.segment}, unit=units.get(m),
                    )
    for dim in report.dimensions[:3]:
        for s in dim.top[:3]:
            for m in (metric_id, weight, numerator):
                if m and (m, dim.dimension) in seg_args:
                    vals = segments[m][dim.dimension].get(s.segment, {})
                    evidence_ids += _evidence_rows(
                        ctx, m, vals, cited_days, seg_args[(m, dim.dimension)], index, tz, {dim.dimension: s.segment},
                        unit=units.get(m),
                    )
    if not evidence_ids:
        return _refuse(f"diagnosis of {metric_id} produced no citable evidence")

    artifact_ids: list[str] = []
    outcome_ids = per_metric_ids.get(metric_id, evidence_ids)
    for f in report.drivers:
        if f.status not in ("implicated", "ruled_out") or f.effect_per_unit is None:
            continue
        ev_ids = [*outcome_ids, *per_metric_ids.get(f.driver, [])]
        tier = _CAUSAL_TIER.get(f.classification or "", "OBSERVATION")
        checks = [dict(r) for r in f.refutations]
        causal = CausalArtifact(
            query={
                "treatment": f.driver, "outcome": metric_id, "classification": f.classification,
                "status": f.status, "causal_path": f.causal_path, "adjustment_set": f.adjustment_set,
                "graph_edges": f.graph_edges, "identified_estimand": f.identified_estimand,
                "direction_evidence": f.direction_evidence, "contribution": f.contribution,
                "contribution_ci": f.contribution_ci, "share_of_change": f.share_of_change,
                "effect_ci": f.effect_ci, "assumptions": f.assumptions, "reason": f.reason,
                "estimation_window": [hist_start.isoformat(), (ev_start - timedelta(days=1)).isoformat()],
                "n_rows": f.n_rows, "inseparable_from": f.inseparable_from,
            },
            evidence_classification=tier if (tier != "CAUSALLY_SUPPORTED" or checks) else "ASSOCIATION",  # type: ignore[arg-type]
            effect_estimate=f.effect_per_unit,
            refutation_checks=checks,
            evidence_ids=ev_ids,
            method=f.estimator or "backdoor.linear_regression",
        )
        artifact_ids.append(_put_derived(ctx, "causal", causal, ev_ids))
    named_text, named_metrics = "", {}
    # The drivers (and the levers below) are compared with what the user compares with: the comparison period, else
    # the period just before the event ("why did CAC increase yesterday" = against the day before). The blended
    # 'usual' level gave a split of +3.9% beside a headline of +6% on the day before (regression Q24).
    previous = [date.fromisoformat(x) for x in ((report.event.previous_period or {}).get("days") or [])] if report.event else []
    reference_days = full_baseline or baseline or previous or sorted(
        {date.fromisoformat(x) for v in (report.event.reference_days.values() if report.event else []) for x in v}
    )

    def cite(metrics: list[str]) -> None:
        for m in metrics:
            if m in series and m not in per_metric_ids:
                ids = _evidence_rows(ctx, m, series[m], cited_days, args_by_metric[m], index, tz, unit=units.get(m))
                per_metric_ids[m] = ids
                evidence_ids.extend(ids)

    ladder_text, ladder_metrics, ladder_stages = _funnel_ladder(
        metric_id, ladder_ends, [*named, *named_parts], lineage, series, history_days, event_days, reference_days
    )
    if ladder_text:
        # The days the stage ratios were read over must be citable, so cite the days the ladder compares.
        cited_days = sorted({*cited_days, *reference_days})
        cite([metric_id, *ladder_stages])
        named_text, named_metrics = ladder_text, dict(ladder_metrics)
    usual = [d for d in history_days if d < ev_start][-14:] or reference_days
    logged = [m for m in change_log if m in series]
    if logged:
        change_text, change_metrics = _change_log_lines(logged, series, event_days, usual, lineage)
        if change_text:
            cited_days = sorted({*cited_days, *usual})
            cite(logged)
            named_text = (named_text + "\n" + change_text) if named_text else change_text
            named_metrics.update(change_metrics)
    if named:
        cite(named)
        versus = f"{reference_days[0]}..{reference_days[-1]}" if len(reference_days) > 1 else (str(reference_days[0]) if reference_days else "")
        driver_text, driver_metrics = _named_driver_lines(
            named, series, event_days, reference_days, lineage, report, history_days
        )
        split_text, split_metrics = _ratio_split(metric_id, named, lineage, series, history_days, event_days, reference_days)
        if split_text:
            driver_text = split_text + "\n" + driver_text
            driver_metrics.update(split_metrics)
        if versus:
            driver_text = f"DRIVERS ARE COMPARED over {', '.join(str(d) for d in event_days)} versus {versus}.\n" + driver_text
        named_text = (named_text + "\n" + driver_text) if named_text else driver_text
        named_metrics.update(driver_metrics)
        if own := _outcome_on_its_normal_days(metric_id, series, event_days, reference_days, history_days, lineage):
            named_text = own + "\n" + named_text
    finding = Finding(
        finding_type="diagnosis",
        statement=report.headline,
        evidence_ids=evidence_ids,
        # the named drivers' averages are derived here: recorded so the answer may print them
        metrics={**_finding_metrics(report), **named_metrics},
    )
    finding_id = _put_derived(ctx, "finding", finding, evidence_ids)
    summary = _summary(report, lineage)
    if named_text:
        summary += "\n" + named_text
    if scope_line := _scope_line(filters):
        # Calls for several entities run side by side; the result must say whose it is.
        summary = scope_line + "\n" + summary
    ctx.deps.scratchpad.note(f"diagnose_metric_change({metric_id}, {ev_start}..{ev_end}) → {report.verdict}: {report.headline}")
    return ToolResult(
        success=True,
        artifact_ids=[finding_id, *artifact_ids, *evidence_ids],
        summary=summary,
        provenance=ArtifactProvenance(
            evidence_ids=evidence_ids, calculation_version=_CALCULATION_VERSION,
            source_metadata={"diagnosis": report.as_dict(), "finding_id": finding_id},
        ),
        warnings=list(report.data_quality),
    )


@functools.wraps(_diagnose)
async def diagnose_metric_change(*args: Any, **kwargs: Any) -> ToolResult:
    """The tool the agent and the executor call; it records how the diagnosis went on the mission, so the answer's
    why-part can be held to it: a diagnosis that never succeeded leaves nothing behind a stated cause."""
    result = await _diagnose(*args, **kwargs)
    ctx = args[0] if args else kwargs.get("ctx")
    counts = getattr(getattr(ctx, "deps", None), "call_counts", None)
    if isinstance(counts, dict):
        if result.success:
            counts[DIAGNOSIS_OK] = 1
            counts.pop(DIAGNOSIS_FAILED, None)
        else:
            counts[DIAGNOSIS_FAILED] = result.summary
    return result


# functools.wraps names the wrapper after the wrapped function; the agent registers this one under its public name.
diagnose_metric_change.__name__ = diagnose_metric_change.__qualname__ = "diagnose_metric_change"

"""DiagnosisToolset — "why did <metric> change?" in one call.

``diagnose_metric_change`` is the composite the agent calls for a why-question.
It plans every candidate from catalogue metadata, fetches through the same
certified Cube path ``metrics_query`` uses (rule 1; budgeted, cached), records
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
import itertools
import math
import statistics
import time
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import CausalArtifact, EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.causal import diagnosis as engine
from seleric_swarm.causal.graph_chart import build_causal_graph_spec
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.diagnosis.adapters.cube_fetch import (  # noqa: F401  (also imported from here by exploration, composition and tests)
    UNSET_SEGMENT,
    _fetch_all,
    _fetch_daily,
    _filters_for,
    _parse_segments,
    _parse_series,
    _prefetch_daily,
    _slice_for,
    _supports_brand,
    _view_of,
)
from seleric_swarm.diagnosis.adapters.cube_series import CubeSeriesAdapter
from seleric_swarm.diagnosis.adapters.legacy_trace import LegacyTrace, OperatorScope, current_trace
from seleric_swarm.diagnosis.adapters.live_record import run_recorded, save_record
from seleric_swarm.diagnosis.adapters.runstore_artifacts import ArtifactRunStore
from seleric_swarm.diagnosis.domain.gather import GatherPlan
from seleric_swarm.diagnosis.domain.types import Scope, SeriesRequest, Window
from seleric_swarm.diagnosis.operators.gather import gather
from seleric_swarm.diagnosis.trace.active import report_failure
from seleric_swarm.diagnosis.trace.run import RecordingSeriesPort
from seleric_swarm.exploration import space
from seleric_swarm.semantic import McpSemanticPort
from seleric_swarm.toolsets import _data
from seleric_swarm.toolsets import policy_config as P

_CALCULATION_VERSION = "diagnosis.v1"
_DEFINITION_BATCH = 10
_DEFINITIONS_TTL_S = 900.0
# The longest event that may be read against its "usual" days when a comparison period is passed (see
# _USUAL_MAX_DAYS for the no-comparison default). Not a limit.
_MAX_EVENT_DAYS = 14
# A window longer than a week is a period, not a day: it is compared with the equal-length period just before it
# (live 2026-10-14 Q4: 14 days against a blended "usual" level, quoted by the answer as the previous period).
_USUAL_MAX_DAYS = 7
_MAX_SEGMENT_ROWS = 6000
_UNSET_SEGMENT = UNSET_SEGMENT

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
            res = await McpSemanticPort(ctx.deps.mcp_client).fetch_metrics(batch)
        except Exception as exc:
            report_failure("definitions_fetch", exc, {"metrics": len(batch)})
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
    """The dimensions to segment by: the catalogue's own plan for the outcome."""
    return space.plan_dimensions(ctx.deps.catalogue, outcome, cap)


def _chain_dims(
    ctx: RunContext[SelericDeps], series_ids: list[str], lineage: dict[str, engine.MetricMeta], cap: int
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """For each (rate, base) pair that could rebuild an additive series as rate x base, the dimensions both are split
    by. Which pairs the data verify is the gather's to say, so every proposed pair whose two series are fetched is
    planned here, from the catalogue, before any data is read."""
    fetched = set(series_ids)
    pairs = dict.fromkeys(
        pair for t in series_ids if lineage.get(t, engine.MetricMeta(t)).additive
        for pair in engine.rate_chain_proposals(t, lineage) if pair[0] in fetched and pair[1] in fetched
    )
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for rate, base in pairs:
        base_dims = set(ctx.deps.catalogue.supported_dimensions_for(base))
        out.append((rate, base, tuple(d for d in _plan_dimensions(ctx, rate, cap) if d in base_dims)))
    return tuple(out)


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
        ids.append(_data.put_evidence(
            ctx, ev, index=index, raw_id=f"raw:{metric_id}:{d}:{d}",
            # The filters the series was fetched with (a channel the question named) are part of what the row
            # means, recorded as metrics_query records them; without them a Meta-filtered diagnosis read as
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
    for bt in report.bridge:
        for k in ("event", "reference", "contribution", "share_of_change", "previous", "contribution_vs_previous"):
            put(f"bridge.{bt.metric}.{k}", getattr(bt, k))
        if abs(bt.reference) > 1e-12:
            put(f"bridge.{bt.metric}.pct_change", bt.event / bt.reference - 1)
        if bt.previous and abs(bt.previous) > 1e-12 and bt.contribution_vs_previous is not None:
            put(f"bridge.{bt.metric}.pct_change_vs_previous", bt.sign * bt.contribution_vs_previous / bt.previous)
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


def _summary(
    report: engine.DiagnosisReport,
    lineage: dict[str, engine.MetricMeta],
    levers: list[str] | None = None,
) -> str:
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
    lines += levers or []
    if report.data_quality:
        lines.append("DATA NOTES: " + " ".join(report.data_quality))
    lines.append(
        "REPORTING RULES: only 'a cause' may be described with caused/drove/because of; arithmetic shares are not "
        "causes; if no cause was identified, say so plainly; every number must come from this result — never "
        "fill a table cell, column or period it does not give; no ids, snake_case labels, z-scores or field names."
    )
    return "\n".join(lines)


def _lever_tree(
    outcome: str, roots: list[str], lineage: dict[str, engine.MetricMeta], usable: Any,
) -> tuple[list[str], list[tuple[str, tuple[str, ...]]]]:
    """The volumes and the rates linking them, out from the outcome's additive parts, from lineage only.

    A rate built on a volume of the tree joins it, and so do its other components: spend brings cost per
    impression (and impressions), cost per click (and clicks); impressions bring clicks per impression and
    any per-impression rate. A rate that shares a component with the outcome in the outcome's own unit is a
    sibling of the outcome (another return on the same spend), not a lever of it.
    """
    out_meta = lineage.get(outcome, engine.MetricMeta(outcome))
    volumes = [r for r in dict.fromkeys(roots) if lineage.get(r, engine.MetricMeta(r)).additive and usable(r)]
    rates: list[tuple[str, tuple[str, ...]]] = []
    frontier = list(volumes)
    for _ in range(P.DIAG_LEVER_DEPTH):
        nxt: list[str] = []
        for x in frontier:
            for r in lineage.values():
                if r.additive or r.id == outcome or x not in r.depends_on or any(r.id == q for q, _ in rates):
                    continue
                if len(r.depends_on) > 2 or not usable(r.id):
                    continue
                if r.unit == out_meta.unit and set(r.depends_on) & set(out_meta.depends_on):
                    continue
                others = [d for d in r.depends_on if d != x]
                if any(not lineage.get(o, engine.MetricMeta(o)).additive or not usable(o) for o in others):
                    continue
                rates.append((r.id, r.depends_on))
                for o in others:
                    if o not in volumes:
                        volumes.append(o)
                        nxt.append(o)
        frontier = nxt
    return volumes[: P.DIAG_LEVER_MAX_VOLUMES], [
        (r, deps) for r, deps in rates if all(d in volumes[: P.DIAG_LEVER_MAX_VOLUMES] for d in deps)
    ][: P.DIAG_LEVER_MAX_RATES]


def _rate_orientation(
    rate: dict[date, float], a: dict[date, float], b: dict[date, float], days: list[date],
) -> tuple[bool, float] | None:
    """(a divides b?, k) with rate = k·a/b (or k·b/a) on every day the three are finite, as the data verify."""
    best: tuple[float, bool, float] | None = None
    for a_over_b in (True, False):
        ks = []
        for d in days:
            r, x, y = rate.get(d), a.get(d), b.get(d)
            if r is None or x is None or y is None or not all(map(math.isfinite, (r, x, y))):
                continue
            num, den = (x, y) if a_over_b else (y, x)
            if abs(num) > 1e-12 and abs(den) > 1e-12 and abs(r) > 1e-12:
                ks.append(r / (num / den))
        if len(ks) < 3:
            continue
        ks.sort()
        k = ks[len(ks) // 2]
        spread = sorted(abs(v / k - 1) for v in ks)[len(ks) // 2]
        if spread <= P.DIAG_IDENTITY_TOLERANCE and (best is None or spread < best[0]):
            best = (spread, a_over_b, k)
    return None if best is None else (best[1], best[2])


def _same_pair(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return all(math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12) for x, y in zip(a, b, strict=True))


def _lever_values(
    volumes: list[str], rates: list[tuple[str, tuple[str, ...]]], series: dict[str, dict[date, float]],
    ref_days: list[date], event_days: list[date], history: list[date],
) -> tuple[dict[str, tuple[float, float]], list[tuple[str, str, str]]]:
    """Period values (reference, event) of every lever, rates as ratios of the period sums of their parts,
    and the splits num = rate × den the data verify."""
    vals: dict[str, tuple[float, float]] = {}
    for v in volumes:
        s = series.get(v) or {}
        if any(d in s for d in event_days) or any(d in s for d in ref_days):
            pair = (sum(s.get(d, 0.0) for d in ref_days), sum(s.get(d, 0.0) for d in event_days))
            if not any(_same_pair(pair, seen) for seen in vals.values()):  # a catalogue twin: the same quantity
                vals[v] = pair
    splits: list[tuple[str, str, str]] = []
    for r, deps in rates:
        rs = series.get(r) or {}
        if len(deps) == 2 and all(d in vals for d in deps):
            o = _rate_orientation(rs, series.get(deps[0]) or {}, series.get(deps[1]) or {}, history)
            if o is None:
                continue
            a_over_b, k = o
            num, den = deps if a_over_b else deps[::-1]
            (n0, n1), (d0, d1) = vals[num], vals[den]
            rate_pair = (k * n0 / d0, k * n1 / d1) if abs(d0) > 1e-12 and abs(d1) > 1e-12 else None
            if rate_pair is not None and not any(_same_pair(rate_pair, seen) for seen in vals.values()):
                vals[r] = rate_pair
                splits.append((num, r, den))
        elif len(deps) == 1 and deps[0] in vals:
            base = series.get(deps[0]) or {}
            per: list[float] = []
            for days in (ref_days, event_days):
                w = [(rs[d], base[d]) for d in days if d in rs and d in base and math.isfinite(rs[d])]
                tot = sum(b for _, b in w)
                per.append(sum(x * b for x, b in w) / tot if tot else math.nan)
            if all(math.isfinite(x) for x in per) and not any(_same_pair((per[0], per[1]), seen) for seen in vals.values()):
                vals[r] = (per[0], per[1])
    return vals, splits


def _lever_lines(
    scope: str, vals: dict[str, tuple[float, float]], splits: list[tuple[str, str, str]],
    lineage: dict[str, engine.MetricMeta],
) -> list[str]:
    def one(m: str) -> str:
        a, b = vals[m]
        return f"{engine._label(m, lineage)} {engine._val(a, m, lineage)} → {engine._val(b, m, lineage)} ({engine._chg(b, a)})"

    def move(m: str) -> float:
        a, b = vals[m]
        return abs(math.log(b / a)) if a > 0 and b > 0 else 0.0

    parts = [f"{engine._label(n, lineage)} {engine._chg(vals[n][1], vals[n][0])} = "
             f"{engine._label(d, lineage)} {engine._chg(vals[d][1], vals[d][0])} × "
             f"{engine._label(r, lineage)} {engine._chg(vals[r][1], vals[r][0])}"
             for n, r, d in splits if n in vals and r in vals and d in vals]
    ranked = sorted((m for m in vals if not lineage.get(m, engine.MetricMeta(m)).additive), key=move, reverse=True)
    lines = [f"{scope}: " + "; ".join(one(m) for m in vals if lineage.get(m, engine.MetricMeta(m)).additive)]
    if parts:
        lines.append(f"  how it splits: {'; '.join(parts)}")
    if ranked:
        lines.append(f"  rates, largest move first: {'; '.join(one(m) for m in ranked)}")
    return lines


async def _levers(
    ctx: RunContext[SelericDeps], report: engine.DiagnosisReport, outcome: str,
    lineage: dict[str, engine.MetricMeta], filters: dict[str, Any], event_days: list[date], ref_days: list[date],
    hist_start: date, history: list[date], index: dict[str, str], tz: Any, units: dict[str, str],
) -> tuple[list[str], list[str], dict[str, float]]:
    """The outcome's parts, the rates that link them and how each moved — in total and in the segments that
    carried the change (live 2026-10-09: "why did ROAS rise vs last week" stopped at margin and spend; the
    analyst answer is one level down: Meta spend +82% bought cheaper impressions (cost per thousand −17%) that
    clicked less (−16%)). Lineage proposes, the data verify; arithmetic only, never causes."""
    if not event_days or not ref_days:
        return [], [], {}
    roots = [t.metric for t in report.decomposition] if report.decomposition else [outcome]
    cat = ctx.deps.catalogue
    basis, _ = cat.date_basis_for(outcome) if hasattr(cat, "date_basis_for") else (None, None)
    # The first dimension in hierarchy order (platform before campaign before ad) with named segments, and in it
    # the segment that added most to the change and the one that offset most: opposite moves (Meta's return falling
    # while Google's rose, live 2026-10-09) are the story, and a "localised" test hides them.
    where = next((d for d in report.dimensions if any(s.segment != _UNSET_SEGMENT for s in d.top)), None)
    named = [s for s in (where.top if where else []) if s.segment != _UNSET_SEGMENT and s.share_of_change is not None]
    picks = [min(named, key=lambda s: s.share_of_change or 0.0, default=None), max(named, key=lambda s: s.share_of_change or 0.0, default=None)]  # the offset first
    segs = list(dict.fromkeys(s.segment for s in picks if s is not None))[: P.DIAG_LEVER_SEGMENTS]
    seg_dim = where.dimension if where and segs else None

    def usable(m: str) -> bool:
        if not cat.has_metric(m) or not _supports_brand(ctx, m):
            return False
        if seg_dim and seg_dim not in set(cat.supported_dimensions_for(m)):
            return False
        b, _ = cat.date_basis_for(m) if hasattr(cat, "date_basis_for") else (None, None)
        return not (basis and b and b != basis)

    volumes, rates = _lever_tree(outcome, roots, lineage, usable)
    if not rates:
        return [], [], {}
    metrics = list(dict.fromkeys([*volumes, *(r for r, _ in rates)]))
    span_start, span_end = hist_start, max(event_days)
    sem = asyncio.Semaphore(6)

    async def guard(coro: Any) -> Any:
        async with sem:
            return await coro

    jobs: list[tuple[str, str | None]] = [(m, None) for m in metrics] + ([(m, seg_dim) for m in metrics] if seg_dim else [])
    window, scope = Window(span_start, span_end), Scope.from_filters(filters)
    results = await CubeSeriesAdapter(ctx, guarded=guard, report_dropped_filters=True).fetch(
        [SeriesRequest(m, window, scope, d) for m, d in jobs]
    )
    scopes: dict[str, dict[str, dict[date, float]]] = {"": {}}
    args_for: dict[tuple[str, str | None], dict[str, Any]] = {}
    for (m, d), result in zip(jobs, results, strict=True):
        if result.error is not None or result.dropped_filters:
            continue
        args_for[(m, d)] = result.query_args
        if d is None:
            scopes[""][m] = dict(result.values)
        else:
            for seg, seg_vals in result.segments.items():
                if seg in segs:
                    scopes.setdefault(seg, {})[m] = dict(seg_vals)
    lines: list[str] = []
    evidence: list[str] = []
    numbers: dict[str, float] = {}
    cited = sorted({*ref_days, *event_days})
    for seg in ["", *segs]:
        series = scopes.get(seg) or {}
        vals, splits = _lever_values(volumes, rates, series, ref_days, event_days, history)
        if not any(not lineage.get(m, engine.MetricMeta(m)).additive for m in vals):
            continue
        label = f"{engine._label(seg_dim, lineage) if seg_dim else ''} {seg}".strip() if seg else "All"
        lines += _lever_lines(label, vals, splits, lineage)
        for m, (a, b) in vals.items():
            numbers[f"lever:{seg or 'all'}:{m}:reference"] = a
            numbers[f"lever:{seg or 'all'}:{m}:event"] = b
            if (m, seg_dim if seg else None) in args_for:
                evidence += _evidence_rows(
                    ctx, m, series.get(m, {}), cited, args_for[(m, seg_dim if seg else None)], index, tz,
                    {seg_dim: seg} if seg and seg_dim else None, unit=units.get(m),
                )
    if not lines:
        return [], [], {}
    head = (
        f"LEVERS (arithmetic links, not causes; {ref_days[0]}..{ref_days[-1]} → {event_days[0]}..{event_days[-1]}): "
        "each part of the outcome, the rates that link the parts and how each moved. Explain the change "
        "through them — which volume grew, and which rate (price, click-through, attention, conversion) moved "
        "against the outcome — first for the segment that moved most against it:"
    )
    return [head, *lines], evidence, numbers


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
    show = lambda v: engine._val(v, metric, lineage)
    return (
        f"THE OUTCOME ON ITS OWN NORMAL DAYS: {engine._label(metric, lineage)} ranged {show(min(usual))}–"
        f"{show(max(usual))} (median {show(statistics.median(usual))}) over {len(usual)} normal days; "
        + (f"{event_days[0]}..{event_days[-1]}: averaged {show(sum(ev) / len(ev))} per day (not the period total). "
           if len(event_days) > 1 else f"{event_days[0]}: {show(sum(ev) / len(ev))}. ")
        + "Say where the day sits in that range; "
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
    vec = lambda v: np.array([v.get(b, 0.0) for b in bases])

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
            texts = [p for p in parts if p is not None]
            if len(texts) == len(parts):
                nested.append(
                    f"{engine._label(m, lineage)} itself = "
                    + " ".join(("× " if s2 > 0 else "÷ ") + engine._label(c, lineage) for c, s2 in sub[0].items()).lstrip("× ")
                    + ": " + "; ".join(t for _, t in sorted(texts, key=lambda p: -abs(p[0])))
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
    shown = lambda m: engine._label(m, lineage)

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
    for lo, hi in itertools.pairwise(seq):
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
        rows.append((eff, (f"{shown_label} {unit}{show(shown_rf)} → {unit}{show(shown_ev)} ({engine._chg(shown_ev, shown_rf)}), "
                          f"moving {shown(outcome)} {math.exp(eff) * 100 - 100:+.1f}%")))
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
    """First and last day of a window. The end day is included, as in ``metrics_query``
    and the resolved-window pin: reading a midnight end as exclusive dropped the last
    day of every pinned multi-day window — "last week" (09-28..10-04) was diagnosed as
    09-28..10-03, and the scope gate then sent the answer back for the missing day
    (live 2026-10-09 MS3-2ec0e4a115)."""
    start, end = start_dt.date(), end_dt.date()
    return (start, end) if start <= end else (end, start)


# --------------------------------------------------------------------------- the tool
def _op(name: str, inputs: dict[str, Any]) -> AbstractContextManager[OperatorScope]:
    """An operator scope on the running investigation's trace, or a do-nothing scope when there is none."""
    trace = current_trace()
    return trace.operator(name, inputs) if trace is not None else nullcontext(OperatorScope())


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
    """Explain why a metric changed: event size, where it sat, evidence-ranked causes. Use for any "why did X
    rise/fall/change" question (metric_id = catalogue id). event_start/event_end default to the question's
    period (else yesterday); claimed_direction is what the user asserts ("down"/"up"), checked against the data;
    filters scope the outcome. Returns the event size vs a same-weekday reference, an exact decomposition,
    segment localisation (incl. Simpson's paradox) and DoWhy-estimated drivers labelled supported_cause /
    likely_contributor / correlation / insufficient_evidence (report faithfully, never upgrade). Period
    comparisons: pass compare_start/compare_end (same length as the event window); a window over a week is
    compared with the equal-length period before it, the difference is split exactly by component and segment,
    and drivers are not estimated. search_breadth 0 (default) fits nearly every question; 1-2 widen the search
    at +7-15 s each, use only after 0 localised nothing. drivers: catalogue ids the user names as possible
    explanations (resolve each first); each is fetched, reported and tested where it can be; report every one.
    For a ratio outcome (ROAS, CAC, a rate) the result adds an exact LEVER LADDER of funnel stages: name the
    stages you want as drivers. With campaign/ad filters it also lists logged edits (budget, status, bid) on
    event vs usual days.
    """
    if (unknown := await _data.reject_unknown_metric(ctx, metric_id)) is not None:
        return unknown
    tz = ctx.deps.as_of.tzinfo
    today = ctx.deps.as_of.date()
    notes: list[str] = []
    default = ctx.deps.as_of - timedelta(days=1)
    start_dt, end_dt = event_start or event_end or default, event_end or event_start or default
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
    elif len(event_days) > _USUAL_MAX_DAYS:
        baseline = [d - timedelta(days=len(event_days)) for d in event_days]
        notes.append(f"compared with the {len(event_days)} days just before it, {baseline[0]}..{baseline[-1]}")
    compared = [*baseline, *full_baseline]
    if compared and max(compared) >= ev_start:
        return _refuse(
            f"the comparison period must end before the event starts ({ev_start}..{ev_end}); it was "
            f"{min(compared)}..{max(compared)}. Pass compare_start/compare_end "
            f"days before {ev_start}, or event_start/event_end for the day to explain",
            error_code="UNSUPPORTED_QUERY",
        )
    hist_start = ev_start - timedelta(days=P.DIAG_HISTORY_DAYS)
    if (trace := current_trace()) is not None:
        trace.window(ev_start, ev_end, baseline=len(compared))
    if baseline or full_baseline:
        hist_start = min(hist_start, min([*baseline[:1], *full_baseline[:1]]) - timedelta(days=_MAX_EVENT_DAYS))
    filters = dict(filters or {})
    if pooled := [k for k, v in filters.items() if isinstance(v, list) and len({str(x) for x in v}) > 1]:
        # One series for several entities: whether each of them moved is not visible in it.
        notes.append(
            f"the {len(filters[pooled[0]])} values of {pooled[0]} are pooled into one series here; call once per "
            "entity to see which of them moved and whether each moved at all"
        )

    marks: list[tuple[str, float]] = [("start", time.perf_counter())]
    definitions = await _all_definitions(ctx)
    if (trace := current_trace()) is not None:
        trace.graph(definitions)
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
    marks.append(("plan", time.perf_counter()))
    env = engine.default_env()
    chain_dims = _chain_dims(ctx, series_ids, lineage, P.DIAG_MAX_DIMENSIONS // 2)
    plan = GatherPlan(
        outcome=metric_id, start=hist_start, end=ev_end, scope=filters, event_days=tuple(event_days),
        partial_days=frozenset(partial), baseline_days=tuple(baseline), claimed_direction=claimed_direction,
        lineage=lineage, series_ids=tuple(series_ids),
        core=frozenset([metric_id, *identity_metrics, *chain_metrics, *drivers, *named, *named_parts]),
        change_log=frozenset(change_log), ladder_ends=frozenset(ladder_ends), bridge_pool=tuple(bridge_pool),
        drivers=tuple(drivers), dims=tuple(dims), chain_dims=chain_dims,
        scope_tokens=frozenset(t for d in _scope_dimensions(ctx) for t in d.lower().split("_") if t),
        max_segment_rows=_MAX_SEGMENT_ROWS,
    )
    recording = RecordingSeriesPort(CubeSeriesAdapter(ctx, guarded=guarded, report_dropped_filters=True))
    gathered = await gather(
        plan, recording, cfg=env.config, scoped=_op, phase=lambda name: marks.append((name, time.perf_counter())),
    )
    if gathered.failure == "fetch_failed":
        return _data.fetch_failure(f"diagnose_metric_change({metric_id})", gathered.failure_detail)
    if gathered.input is None:
        return _refuse(f"no daily data for {metric_id} over {hist_start}..{ev_end}")
    inp = gathered.input
    series, segments = inp.series, inp.segments
    args_by_metric, seg_args, units = gathered.series_args, gathered.segment_args, gathered.units
    history_days, weight, numerator = gathered.history_days, gathered.weight, gathered.numerator
    quality = gathered.notes
    for _ in inp.candidate_drivers:
        if not ctx.deps.budget.consume("causal_queries").ok:
            inp.candidate_drivers = inp.candidate_drivers[: max(0, len(inp.candidate_drivers) - 1)]
    # The plan the record keeps is the one that was run: a replay gathers from it, so the budget's cut is part of it.
    plan = replace(plan, drivers=tuple(inp.candidate_drivers))
    with _op("analyse", {"candidate_drivers": len(inp.candidate_drivers), "history_days": len(history_days)}) as scope:
        # The engine runs as the replayable runner: its stages, hypotheses and failures appear on the live trace as they
        # happen, and the record of the run (series, estimates, clock, events, report) is kept for replay.
        report, record = await run_recorded(
            inp, plan, recording, config=env.config, estimator=env.estimator, trace=trace
        )
        scope.result = {"verdict": report.verdict, "drivers": len(report.drivers), "dimensions": len(report.dimensions)}
    save_record(
        ArtifactRunStore(
            ctx.deps.artifact_store, workspace_id=ctx.deps.principal.workspace_id, mission_id=ctx.deps.mission_id,
            thread_id=ctx.deps.thread_id or None,
        ),
        record,
    )
    marks.append(("engine", time.perf_counter()))
    report.data_quality = [*notes, *quality, *report.data_quality]

    # ---- evidence + derived artifacts ------------------------------------------------
    index = _data.evidence_index(ctx)
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
            for mm in (metric_id, weight, numerator):
                if mm and (mm, dim.dimension) in seg_args:
                    vals = segments[mm][dim.dimension].get(s.segment, {})
                    evidence_ids += _evidence_rows(
                        ctx, mm, vals, cited_days, seg_args[(mm, dim.dimension)], index, tz, {dim.dimension: s.segment},
                        unit=units.get(mm),
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
    # One chart for the union of DoWhy DAGs so the UI can show causal structure.
    graph_spec = build_causal_graph_spec(report, outcome=metric_id)
    if graph_spec is not None:
        chart = ctx.deps.artifact_store.put(
            Artifact(
                workspace_id=ctx.deps.principal.workspace_id,
                artifact_type="chart_spec",
                payload=graph_spec,
                classification="derived",
                evidence_ids=list(evidence_ids),
                provenance=ArtifactProvenance(
                    evidence_ids=list(evidence_ids), calculation_version=_CALCULATION_VERSION
                ),
                mission_id=ctx.deps.mission_id,
            )
        )
        artifact_ids.append(chart.id)
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
    lever_ref = baseline or [
        date.fromisoformat(x)
        for x in ((report.event.previous_period.get("days") or []) if report.event else [])
    ]
    marks.append(("evidence", time.perf_counter()))
    with _op("levers", {"drivers": len(report.drivers)}):
        lever_lines, lever_ids, lever_numbers = await _levers(
            ctx, report, metric_id, lineage, filters, event_days, sorted(lever_ref), hist_start, history_days, index, tz,
            units,
        )
    evidence_ids += lever_ids
    marks.append(("levers", time.perf_counter()))
    finding = Finding(
        finding_type="diagnosis",
        statement=report.headline,
        evidence_ids=evidence_ids,
        # the named drivers' averages are derived here: recorded so the answer may print them
        metrics={**_finding_metrics(report), **named_metrics, **lever_numbers},
    )
    finding_id = _put_derived(ctx, "finding", finding, evidence_ids)
    summary = _summary(report, lineage, lever_lines)
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
            source_metadata={
                "diagnosis": report.as_dict(), "finding_id": finding_id,
                "phase_ms": {b[0]: round((b[1] - a[1]) * 1000) for a, b in itertools.pairwise(marks)},
            },
        ),
        warnings=list(report.data_quality),
    )


@functools.wraps(_diagnose)
async def diagnose_metric_change(*args: Any, **kwargs: Any) -> ToolResult:
    """The tool the agent and the executor call. It runs the diagnosis under an investigation trace: an id on every log
    line and span, ordered events on the mission stream, and the id on the result so the answer can be traced back."""
    ctx = args[0] if args else kwargs.get("ctx")
    metric = args[1] if len(args) > 1 else kwargs.get("metric_id", "")
    mission_id = str(getattr(getattr(ctx, "deps", None), "mission_id", "") or "")
    trace = LegacyTrace(mission_id, str(metric), kwargs.get("filters") or {})
    reason = "error"
    with trace.activate():
        trace.started()
        try:
            result = await _diagnose(*args, **kwargs)
            diagnosis = result.provenance.source_metadata.get("diagnosis") or {}
            reason = str(diagnosis.get("verdict") or ("complete" if result.success else (result.error_code or "failed")))
        finally:
            trace.finished(reason)
    result.provenance.source_metadata["investigation_id"] = trace.investigation_id
    return result


# functools.wraps names the wrapper after the wrapped function; the agent registers this one under its public name.
diagnose_metric_change.__name__ = diagnose_metric_change.__qualname__ = "diagnose_metric_change"

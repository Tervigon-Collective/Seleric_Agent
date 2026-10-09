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
import math
import time
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import CausalArtifact, EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import SelericDeps
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
    out, dropped = [], []
    for k, v in (filters or {}).items():
        if v in (None, "", []):
            continue
        if supported and k not in supported:
            dropped.append(k)
            continue
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
            provenance=ArtifactProvenance(calculation_version=_CALCULATION_VERSION),
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


def _named_driver_lines(
    named: list[str],
    series: dict[str, dict[date, float]],
    event_days: list[date],
    reference_days: list[date],
    lineage: dict[str, engine.MetricMeta],
    report: engine.DiagnosisReport,
) -> tuple[str, dict[str, float]]:
    """The drivers the user asked about, each with its move over the event vs the reference days and its test
    verdict — so the answer addresses every hypothesis the user raised, largest move first. An average per day
    keeps windows of different lengths comparable; it is a move, not a contribution, unless a verdict says so."""
    verdicts = {f.driver: f for f in report.drivers}
    rows: list[tuple[float, str]] = []
    figures: dict[str, float] = {}
    for m in named:
        vals = series.get(m) or {}
        ev = [vals[d] for d in event_days if d in vals]
        rf = [vals[d] for d in reference_days if d in vals]
        label = engine._label(m, lineage)
        if not ev or not rf:
            rows.append((-1.0, f"{label}: no data over these days, so it could not be checked"))
            continue
        e_avg, r_avg = sum(ev) / len(ev), sum(rf) / len(rf)
        rel = (e_avg / r_avg - 1) if abs(r_avg) > 1e-12 else math.nan
        figures[f"{m} | event average per day"] = e_avg
        figures[f"{m} | reference average per day"] = r_avg
        if math.isfinite(rel):
            figures[f"{m} | change"] = rel
        move = f"{label} {engine._val(r_avg, m, lineage)} → {engine._val(e_avg, m, lineage)} per day ({engine._chg(e_avg, r_avg)})"
        f = verdicts.get(m)
        if f is not None and f.status == "implicated" and f.classification:
            verdict = f"tested: {f.classification.replace('_', ' ')}"
        elif f is not None:
            verdict = f"tested: {f.status.replace('_', ' ')}" + (f" ({f.reason})" if f.reason else "")
        else:
            verdict = "described, not tested as a cause (a rate, or a period comparison)"
        rows.append((abs(rel) if math.isfinite(rel) else -1.0, f"{move} — {verdict}"))
    rows.sort(key=lambda r: -r[0])
    return (
        "DRIVERS THE USER ASKED ABOUT (address every one, in this order — the size of each move; only a tested "
        "cause may be called a cause): " + "; ".join(text for _, text in rows) + "."
    ), figures


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
    """An exact multiplicative split of a ratio outcome through the rates the user named.

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
    if out is None or lineage.get(outcome, engine.MetricMeta(outcome)).additive:
        return "", {}
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

    def split(target: np.ndarray, cands: list[str], exact: bool) -> tuple[dict[str, int], dict[str, float]] | None:
        best: tuple[tuple[float, ...], dict[str, int], dict[str, float]] | None = None
        for signs in itertools.product((-1, 0, 1), repeat=len(cands)):
            chosen = {m: sg for m, sg in zip(cands, signs, strict=True) if sg}
            if not chosen or np.linalg.matrix_rank(np.array([vec(forms[m][1]) for m in chosen])) < len(chosen):
                continue
            residual = target - sum(sg * vec(forms[m][1]) for m, sg in chosen.items())
            res = {b: float(e) for b, e in zip(bases, residual, strict=True) if abs(e) > 1e-9}
            if exact and res:
                continue
            rv = value(1.0, res, reference_days) if res else 1.0
            closeness = abs(math.log(rv)) if rv else math.inf
            score = (len(res), len(chosen), closeness)
            if best is None or score < best[0]:
                best = (score, chosen, res)
        return None if best is None else (best[1], best[2])

    cands = list(forms)[:6]
    top = split(vec(out[1]), cands, exact=False)
    if top is None:
        return "", {}
    chosen, res_form = top

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
        f"EXACT SPLIT OF THE CHANGE (a multiplicative identity of the catalogue's definitions, verified on the "
        f"data; the effects multiply to the total, so rank them by size): {engine._label(outcome, lineage)} = "
        f"{formula} (× a constant). " + "; ".join(t for _, t in rows)
        + f"; together {engine._label(outcome, lineage)} {math.exp(total) * 100 - 100:+.1f}%."
        + (" " + " ".join(n + "." for n in nested) if nested else "")
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
async def diagnose_metric_change(
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
        # The question named a period: hold the call to it (same rule as query_metrics).
        if (pinned := semantic._pin_to_resolved_window(ctx, start_dt, end_dt)) is not None:
            start_dt, end_dt, note = pinned
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
    if compare_start is not None or compare_end is not None:
        c_start, c_end = _inclusive_days(compare_start or compare_end, compare_end or compare_start)  # type: ignore[arg-type]
        c_days = [c_start + timedelta(days=i) for i in range((c_end - c_start).days + 1)]
        if running_left_out and len(c_days) == len(event_days) + running_left_out:
            # A period to date against its counterpart: the running day left the event, so its
            # counterpart (the comparison's last day) leaves too — the same span from the start
            # (live 2026-10-09: 10-05..10-08 was compared with 09-29..10-02, not 09-28..10-01).
            c_days = c_days[: len(event_days)]
            notes.append(f"compared with {c_days[0]}..{c_days[-1]}, the same days of the comparison period")
        if len(c_days) != len(event_days):
            notes.append(
                f"the comparison period {c_start}..{c_end} has {len(c_days)} days and the period asked about "
                f"{len(event_days)}; compared with the {len(event_days)} days ending {c_end} so totals are like for like"
            )
            c_days = [c_end - timedelta(days=i) for i in range(len(event_days) - 1, -1, -1)]
        baseline = c_days
    elif len(event_days) > _MAX_EVENT_DAYS:
        baseline = [d - timedelta(days=len(event_days)) for d in event_days]
    if baseline and max(baseline) >= ev_start:
        return _refuse(f"the comparison period must end before {ev_start}", error_code="UNSUPPORTED_QUERY")
    hist_start = ev_start - timedelta(days=P.DIAG_HISTORY_DAYS)
    if baseline:
        hist_start = min(hist_start, baseline[0] - timedelta(days=_MAX_EVENT_DAYS))
    filters = dict(filters or {})

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
    series_ids = list(dict.fromkeys(
        [metric_id, *identity_metrics, *chain_metrics, *drivers, *named, *named_parts, *bridge_pool]
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
            if m in bridge_pool and m not in (metric_id, *identity_metrics, *chain_metrics, *drivers, *named, *named_parts):
                # An unfiltered total cannot be a term of a filtered outcome.
                bridge_pool.remove(m)
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
    if named:
        for m in named:
            if m in series and m not in per_metric_ids:
                ids = _evidence_rows(ctx, m, series[m], cited_days, args_by_metric[m], index, tz, unit=units.get(m))
                per_metric_ids[m] = ids
                evidence_ids += ids
        reference_days = baseline or sorted(
            {date.fromisoformat(x) for v in (report.event.reference_days.values() if report.event else []) for x in v}
        )
        named_text, named_metrics = _named_driver_lines(named, series, event_days, reference_days, lineage, report)
        split_text, split_metrics = _ratio_split(metric_id, named, lineage, series, history_days, event_days, reference_days)
        if split_text:
            named_text = split_text + "\n" + named_text
            named_metrics.update(split_metrics)
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

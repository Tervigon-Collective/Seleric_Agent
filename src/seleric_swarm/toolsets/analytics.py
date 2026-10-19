"""Evidence plumbing for the chart tool.

``generate_visualization`` plots evidence a data tool already wrote to the ``ArtifactStore``; the helpers load it,
repair mistyped ids and unwrap finding artifacts to the measurements they cite. The tool never fetches data and never
calls another tool. The arithmetic behind a chart lives in ``analytics/visualization.py``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.analytics.chart_vocabulary import CHART_TYPES, normalize_chart_type
from seleric_swarm.analytics.grain import CALCULATION_VERSION
from seleric_swarm.analytics.visualization import generate_visualization_spec
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance


def _provenance(evidence_ids: list[str]) -> ArtifactProvenance:
    return ArtifactProvenance(evidence_ids=list(evidence_ids), calculation_version=CALCULATION_VERSION)


def _refuse(
    summary: str, *, error_code: str, retryable: bool = False, warnings: Sequence[str] = ()
) -> ToolResult:
    return ToolResult(
        success=False,
        summary=summary,
        error_code=error_code,
        retryable=retryable,
        warnings=list(warnings),
    )


_AVAILABLE_EVIDENCE_SHOWN = 60


# Shortest id fragment accepted as a repair candidate. Long enough that a
# coincidental prefix collision across a mission's evidence is implausible,
# short enough to catch the dropped-trailing-character transcription.
_MIN_ID_PREFIX_CHARS = 8


def _repair_ids(ctx: RunContext[SelericDeps], requested: list[str]) -> tuple[list[str], dict[str, str]]:
    """Repair mistyped artifact ids, and say which id each request resolved to.

    ``evidence_ids`` is an opaque-40-char-token channel: the model must echo back
    the exact ids a previous tool returned, and at real cardinality it
    mis-transcribes them (live 2026-10-06 MS3-167d9f4838: 47 ids copied into
    ``run_python``, one character dropped from one of them, and the whole
    computation lost). ``instructions.py`` already forbids this pattern and the
    model did it anyway, because the signature *mandates* it — so the repair
    belongs here, where a dropped character is recoverable by construction.

    A repair is applied only when it is **unambiguous**: exactly one evidence
    artifact in this mission has the requested id as a prefix (or is a prefix of
    it, for a spurious trailing character). Two candidates resolve to nothing —
    guessing between them is how a finding ends up citing the wrong evidence
    (non-negotiable rule 6).
    """
    resolved = list(requested)
    known = ctx.deps.artifact_store.get_many(list(requested))
    found = {a.id for a in known}
    repairs: dict[str, str] = {}
    missing = [aid for aid in requested if aid not in found]
    if not missing:
        return resolved, repairs

    evidence_ids_in_mission = [
        a.id
        for a in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id)
        if a.artifact_type == "evidence"
    ]
    for aid in missing:
        if len(aid) < _MIN_ID_PREFIX_CHARS:
            continue
        candidates = [
            candidate
            for candidate in evidence_ids_in_mission
            if candidate.startswith(aid) or aid.startswith(candidate)
        ]
        if len(candidates) == 1:
            repairs[aid] = candidates[0]
    if not repairs:
        return resolved, repairs
    resolved = [repairs.get(aid, aid) for aid in requested]
    return resolved, repairs


def _available_evidence(ctx: RunContext[SelericDeps]) -> str:
    """The evidence this mission already holds, so a refused call can be fixed.

    Live 2026-10-04 (MS3-34e7eb26aa): a call with ids that did not exist was
    refused with only the bad ids, and the model guessed again — twelve times.
    Naming the real ids (with what each one is) turns the next call into a fix.
    """
    rows: list[str] = []
    for artifact in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id):
        if artifact.artifact_type != "evidence":
            continue
        try:
            ev = EvidenceArtifact.model_validate(artifact.payload)
        except Exception:
            continue
        period = f"{ev.period_start.date()}" + (
            "" if ev.period_end.date() == ev.period_start.date() else f"..{ev.period_end.date()}"
        )
        dims = ", ".join(f"{k}={v}" for k, v in sorted(ev.dimensions.items()))
        rows.append(f"{artifact.id} ({ev.metric_id} {period}{' ' + dims if dims else ''})")
    if not rows:
        return " This mission holds no evidence yet: fetch it with metrics_query first."
    more = "" if len(rows) <= _AVAILABLE_EVIDENCE_SHOWN else f" …(+{len(rows) - _AVAILABLE_EVIDENCE_SHOWN} more)"
    return " Evidence available in this mission: " + "; ".join(rows[:_AVAILABLE_EVIDENCE_SHOWN]) + more


# Derived artifacts that cite the measurements they were built from.
_DERIVED_TYPES = frozenset({"finding", "chart_spec"})


def _finding_backing_ids(artifact: Artifact) -> list[str]:
    """Evidence ids a derived artifact (finding, chart spec) cites, preferring the
    artifact-level chain.

    Findings and chart specs are derived; their ``evidence_ids`` (or payload copy)
    point at the measurements that still have to be loaded for any chart or
    calculation. Empty when the row is not derived or cites nothing.
    """
    if artifact.artifact_type not in _DERIVED_TYPES:
        return []
    ids = list(artifact.evidence_ids or [])
    if ids:
        return ids
    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    raw = payload.get("evidence_ids") or []
    return [str(x) for x in raw if x]


def _unwrap_findings_to_evidence_ids(
    ctx: RunContext[SelericDeps], requested: list[str]
) -> tuple[list[str], dict[str, list[str]]]:
    """Replace finding ids with the evidence they cite; leave evidence as-is.

    Live 2026-10-08 (MS3-7748dee188): the model called ``generate_visualization``
    with a ``prefetched_lookup`` finding id. Charts need the backing measurements,
    not the derived summary — expanding here is the same class of recovery as
    ``_repair_ids`` (opaque id channel, model held the wrong token). Scoring
    tools get the same expansion through ``_load_evidence``: they score the
    finding's backing measurements, never the finding itself.
    """
    if not requested:
        return [], {}
    known = {a.id: a for a in ctx.deps.artifact_store.get_many(list(requested))}
    expanded: list[str] = []
    replacements: dict[str, list[str]] = {}
    for aid in requested:
        artifact = known.get(aid)
        if artifact is None:
            expanded.append(aid)
            continue
        backing = _finding_backing_ids(artifact)
        if not backing:
            expanded.append(aid)
            continue
        replacements[aid] = backing
        expanded.extend(backing)
    # Preserve first-seen order: a finding that cites the same evidence as an
    # earlier id must not double-plot the series.
    deduped = list(dict.fromkeys(expanded))
    return deduped, replacements


def _load_evidence(
    ctx: RunContext[SelericDeps], evidence_ids: list[str], *, warnings: Sequence[str] = ()
) -> tuple[list[EvidenceArtifact], list[str], ToolResult | None]:
    """Resolve artifact ids to typed evidence, or a refusal explaining why not.

    Returns ``(evidence, resolved_ids, refusal)``. ``resolved_ids`` is what the
    request actually became — the same length and order as the input, so callers
    can rebind over ``evidence_ids`` and have every downstream ``zip`` and
    ``_write_finding`` cite the artifact that was really read. Passing the
    *original* list back would cite an id that does not exist the moment a
    mistyped one is repaired.

    A missing id is reported rather than skipped: silently computing over a
    subset of what the agent asked for is how a finding ends up citing
    evidence that didn't back it (non-negotiable rule 6).

    ``retryable=True`` on an input-shaped refusal is deliberate and load-bearing.
    A wrong or mistyped id is the most recoverable error class there is — the
    fix is a corrected call, not a replay — but ``ToolResult.retryable``
    defaults to ``False`` and ``instructions.py`` tells the model "a
    non-retryable error does not justify replaying the same call". Live
    2026-10-06 (MS3-167d9f4838): one dropped character produced
    ``retryable=False``, the model correctly declined to re-call, abandoned the
    mission, and shipped half the question as ``completed``. ``repeat_guard``
    keys off this same flag to re-execute rather than replay, so a corrected id
    is a real retry, not a loop.
    """
    if not evidence_ids:
        return (
            [],
            [],
            _refuse(
                "no evidence_ids supplied",
                error_code="INSUFFICIENT_EVIDENCE",
                retryable=True,
                warnings=warnings,
            ),
        )

    resolved_ids, repairs = _repair_ids(ctx, list(evidence_ids))
    # A finding id stands for the measurements it cites: compute over those. Refusing it cost a round trip per
    # call, and live the model often never recovered (2026-10-08/09: 34 of 40 analyze calls failed this way, then
    # run_python was fed hand-typed numbers). A derived artifact that cites nothing is still refused below.
    resolved_ids, unwrapped = _unwrap_findings_to_evidence_ids(ctx, resolved_ids)
    if unwrapped:
        _note_repaired_ids(
            ctx, "; ".join(f"{fid} -> its {len(ids)} evidence rows" for fid, ids in sorted(unwrapped.items()))
        )
    artifacts = ctx.deps.artifact_store.get_many(resolved_ids)
    found = {a.id for a in artifacts}
    missing = [aid for aid in resolved_ids if aid not in found]
    if missing:
        return (
            [],
            [],
            _refuse(
                f"evidence not found in store: {', '.join(missing)}." + _available_evidence(ctx),
                error_code="INSUFFICIENT_EVIDENCE",
                retryable=True,
                warnings=warnings,
            ),
        )

    evidence: list[EvidenceArtifact] = []
    for artifact in artifacts:
        if artifact.artifact_type != "evidence":
            backing = _finding_backing_ids(artifact)
            hint = (
                f" Use its backing evidence_ids instead: {', '.join(backing)}."
                if backing
                else _available_evidence(ctx)
            )
            return (
                [],
                [],
                _refuse(
                    f"artifact {artifact.id} is artifact_type={artifact.artifact_type!r}, not evidence."
                    + hint,
                    error_code="INSUFFICIENT_EVIDENCE",
                    # Finding-for-evidence is recoverable the same way a mistyped
                    # id is: next call with the backing evidence_ids.
                    retryable=bool(backing),
                    warnings=warnings,
                ),
            )
        try:
            evidence.append(EvidenceArtifact.model_validate(artifact.payload))
        except Exception as exc:  # never raise across the tool boundary
            return (
                [],
                [],
                _refuse(
                    f"artifact {artifact.id} payload is not a valid EvidenceArtifact: {exc}",
                    error_code="INSUFFICIENT_EVIDENCE",
                    warnings=warnings,
                ),
            )
    if repairs:
        # Say what was repaired: a silent substitution would leave the model
        # reasoning about ids that were never in its hands.
        _note_repaired_ids(
            ctx, "; ".join(f"{bad} -> {good}" for bad, good in sorted(repairs.items()))
        )
    return evidence, resolved_ids, None


def _note_repaired_ids(ctx: RunContext[SelericDeps], note: str) -> None:
    """Record a repaired-id substitution on the mission scratchpad.

    Best-effort: the scratchpad is a working note, not part of the evidence
    chain, so a failure here must never fail the load it was describing.
    """
    with contextlib.suppress(Exception):
        ctx.deps.scratchpad.note(f"[evidence ids auto-repaired] {note}")


def _write_finding(
    ctx: RunContext[SelericDeps],
    *,
    finding_type: str,
    statement: str,
    evidence_ids: list[str],
    metrics: dict[str, float],
) -> str:
    finding = Finding(
        finding_type=finding_type,
        statement=statement,
        evidence_ids=list(evidence_ids),
        metrics=metrics,
    )
    artifact = ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type="finding",
            payload=finding.model_dump(mode="json"),
            classification="derived",
            evidence_ids=list(evidence_ids),
            provenance=_provenance(evidence_ids),
            mission_id=ctx.deps.mission_id,
        )
    )
    return artifact.id


async def generate_visualization(
    ctx: RunContext[SelericDeps],
    evidence_ids: list[str],
    intent: str,
    title: str = "Visualization",
    chart_type: str | None = None,
) -> ToolResult:
    """Generate a visualization specification for a given set of evidence.

    This tool should only be used when:
    - Comparing > 3 categories
    - Showing trends over time (time series)
    - Showing compositions (pie/donut)
    - Showing distributions or relationships

    Do NOT use this tool for single numbers or simple KPI requests.

    ``chart_type`` selects the form. Supported forms are::

        line, area, bar, horizontal_bar, stacked_bar, grouped_bar, combo,
        pie, donut, treemap, funnel, waterfall, scatter, radar, heatmap

    Choose by what the question asks: a trend over time -> line (area for a
    cumulative level); a ranking or top-N -> horizontal_bar; a handful of
    categories -> bar; a measure split by category over time -> stacked_bar
    (parts that add up) or line; share of a whole -> donut (up to 6 slices) or
    treemap (many); two measures per entity -> grouped_bar, or scatter to see
    how they relate; one volume and one rate over time -> combo; ordered
    stages that drop off -> funnel; signed steps from a start to a total ->
    waterfall; many measures per entity -> heatmap or radar. Omit it to get the
    form the evidence shape supports best.

    Measures that differ in kind or size (money, counts, averages) are drawn in
    separate panels automatically, never on one shared scale.

    Pass the form the user asked for, matching that list ("stacked bar" and
    "stackedBar" both resolve to ``stacked_bar``). An unknown form is refused,
    never silently replaced.

    ``intent`` is a short description of what the chart should show; it never
    selects the form.

    ``evidence_ids`` may include finding artifacts: they are expanded to the
    measurements they cite before the chart is built (charts plot evidence,
    not derived summaries).
    """
    evidence_ids, finding_unwraps = _unwrap_findings_to_evidence_ids(ctx, list(evidence_ids))
    if finding_unwraps:
        _note_repaired_ids(
            ctx,
            "findings unwrapped for chart: "
            + "; ".join(f"{fid} -> [{', '.join(eids)}]" for fid, eids in sorted(finding_unwraps.items())),
        )
    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    # The form is the caller's explicit choice. An unrecognized one is refused
    # with the supported list — never remapped to something else in silence.
    if chart_type is not None and normalize_chart_type(chart_type) is None:
        return _refuse(
            f"Unsupported chart_type {chart_type!r}. Supported forms: "
            f"{', '.join(CHART_TYPES)}.",
            error_code="VISUALIZATION_FAILED",
        )
    catalogue = ctx.deps.catalogue
    metric_ids = {e.metric_id for e in evidence}
    spec = generate_visualization_spec(
        evidence, intent, title, chart_type=chart_type,
        aggregations={m: a for m in metric_ids if (a := catalogue.aggregation_for(m))},
        labels={m.id: m.label for m in catalogue.metrics if m.id in metric_ids and m.label},
    )

    if "error" in spec:
        return _refuse(spec["error"], error_code="VISUALIZATION_FAILED")

    chart_warnings = list(spec.get("warnings") or [])

    # Idempotent: the same chart over the same evidence is one artifact. A
    # revision or a recovery retry of the mission re-asks for the chart it
    # already has; writing it again showed duplicate charts on the answer.
    wanted = set(evidence_ids)
    artifact = next(
        (
            a
            for a in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id)
            if a.artifact_type == "chart_spec" and a.payload == spec and set(a.evidence_ids) == wanted
        ),
        None,
    ) or ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type="chart_spec",
            payload=spec,
            classification="derived",
            evidence_ids=list(evidence_ids),
            provenance=_provenance(evidence_ids),
            mission_id=ctx.deps.mission_id,
        )
    )

    note = ""
    if chart_warnings:
        note = " Adjusted for readability: " + "; ".join(chart_warnings) + "."
    return ToolResult(
        success=True,
        artifact_ids=[artifact.id],
        summary=(
            f"Generated {spec['chart_type']} chart visualization. It is attached to your answer "
            "automatically: do not link, embed or describe it as an image in your answer, and "
            "do not call show_chart again for the same evidence."
            + note
        ),
        provenance=_provenance(evidence_ids),
        warnings=[f"chart adjusted: {w}" for w in chart_warnings],
    )

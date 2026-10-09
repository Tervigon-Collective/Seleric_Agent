"""V3-native checks that produce the inputs ``score_trust``/``decide_verdict`` consume.

Why this layer exists
---------------------
swarm_v2's ``score_trust`` reads ``ValidatorOutcome.score_signals`` emitted by
11 validators under ``agents/skeptic/validators/``, most of which depend on
swarm_v2 plumbing that V3 does not have (``deps.stats``,
``deps.resolved_causal_service()``, ``deps.rules``, a drift monitor, the
``SkepticContext``/claim model). Porting all 11 is not this sprint's job and
several have no V3 counterpart at all.

So the split is: **the scoring arithmetic ports unchanged** (``trust.py``,
``verdict.py`` — that is where the regression risk lives), and *what feeds it*
is rewritten here against V3 artifacts. Each check reads the mission's
``Artifact``s out of the ``ArtifactStore`` and emits the same
``score_signals``/``challenges``/``gaps`` vocabulary the ported functions
already expect.

Deliberate difference from the original
---------------------------------------
``agents/skeptic/contracts.py::Challenge`` has no contradiction-type field —
the type is smuggled through ``detail["contradiction_type"]`` and read back by
exact key at ``agents/skeptic/graph.py:229``. That coupling is invisible to a
type checker and easy to drop in a port, so ``Challenge`` here carries
``contradiction_type`` as a real field.

Signals this layer cannot compute are simply not emitted. That is correct, not
a gap: ``score_trust`` skips a profile dimension with no feeder and renormalizes
the remaining weights, so an uncomputable dimension neither inflates nor
deflates the score. V3 currently has no temporal-order or graph-path check
(swarm_v2's live at ``agents/diagnostic/causal/estimator.py:112-124``), so
``temporal_validity`` and ``graph_plausibility`` are among the absent ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from datetime import UTC, date, datetime, timedelta

from seleric_swarm.services.elapsed import in_progress_day, same_span
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from seleric_swarm.agent.artifacts import CausalArtifact, EvidenceArtifact, PredictionArtifact
from seleric_swarm.toolsets import policy_config as policy

# Temporal grain hierarchy: finer grain can satisfy coarser grain requests
# via valid aggregation (sum for additive metrics, avg for rates, etc.)
TEMPORAL_HIERARCHY: dict[str, list[str]] = {
    "day": ["week", "month", "quarter", "year"],
    "week": ["month", "quarter", "year"],
    "month": ["quarter", "year"],
    "quarter": ["year"],
}


def _grain_satisfies(requested: str, available: str) -> bool:
    """Return True if available grain can satisfy requested grain via aggregation."""
    if requested == available:
        return True
    return requested in TEMPORAL_HIERARCHY.get(available, [])

if TYPE_CHECKING:
    from seleric_swarm.agent.dependencies import SelericDeps
    from seleric_swarm.agent.output import MissionResult
    from seleric_swarm.conversations.contracts import Artifact

Severity = Literal["info", "warning", "blocking"]
CheckStatus = Literal["OK", "WEAK", "REJECTED", "UNAVAILABLE", "NOT_APPLICABLE", "INSUFFICIENT"]
ClaimType = Literal["numeric", "causal", "forecast", "default"]


@dataclass
class Challenge:
    category: str
    severity: Severity
    description: str
    evidence_refs: list[str] = field(default_factory=list)
    # Explicit, unlike the original's detail["contradiction_type"] smuggling.
    contradiction_type: str | None = None


@dataclass
class EvidenceGap:
    description: str
    blocking: bool = False
    priority: int = 5


@dataclass
class AlternativeHypothesis:
    hypothesis: str
    status: Literal["open", "supported", "eliminated"] = "open"
    # Default 5 is BELOW the >=6 verdict trigger, matching
    # agents/skeptic/contracts.py:445-453. An alternative only forces REVISE
    # when something deliberately raises its priority.
    priority: int = 5


@dataclass
class CheckOutcome:
    check: str
    status: CheckStatus = "OK"
    score_signals: dict[str, float] = field(default_factory=dict)
    challenges: list[Challenge] = field(default_factory=list)
    gaps: list[EvidenceGap] = field(default_factory=list)
    methodological_issues: list[str] = field(default_factory=list)

    @property
    def has_blocking(self) -> bool:
        return any(ch.severity == "blocking" for ch in self.challenges)


def _payload(artifact: Artifact, model: type[BaseModel]) -> Any | None:
    try:
        return model.model_validate(artifact.payload)
    except Exception:
        return None


def claim_type_for(artifacts: list[Artifact]) -> ClaimType:
    """Pick the trust profile the way swarm_v2 picked it from ``claim.claim_type``.

    V3 has no claim object, so the mission's own artifacts decide: a causal
    conclusion is weighted as causal, a prediction as forecast, otherwise
    numeric. ``default`` only when the mission produced neither evidence nor
    findings.
    """
    types = {a.artifact_type for a in artifacts}
    if "causal" in types:
        return "causal"
    if "prediction" in types:
        return "forecast"
    if types & {"evidence", "finding"}:
        return "numeric"
    return "default"


def check_evidence(artifacts: list[Artifact]) -> CheckOutcome:
    """Evidence present, valued, and fresh enough to stand behind.

    Two distinct "no evidence" cases, deliberately scored differently:

    * The mission produced **nothing at all** — no artifacts of any kind. There
      is no claim to validate, so this is ``NOT_APPLICABLE``, not a failure.
      Rule 6 binds numerical claims to evidence; a mission that made no
      numerical claim owes none.
    * The mission produced **derived artifacts but no evidence** — a finding,
      causal or prediction artifact whose ``evidence_ids`` point at nothing in
      the store. That is a real rule-6 problem, but it is a *blocking gap*
      (REVISE — "the claim might be true, the evidence is incomplete") rather
      than a REJECT, which is reserved for evidence that contradicts the claim
      or fails schema validation.
    """
    rows = [a for a in artifacts if a.artifact_type == "evidence"]
    if not rows:
        if not artifacts:
            return CheckOutcome(check="evidence", status="NOT_APPLICABLE")
        return CheckOutcome(
            check="evidence",
            status="INSUFFICIENT",
            gaps=[
                EvidenceGap(
                    description=(
                        "mission produced derived artifacts but no evidence artifacts "
                        "to back them"
                    ),
                    blocking=True,
                    priority=9,
                )
            ],
            score_signals={"evidence_quality": 0.0},
        )

    parsed = [(a, _payload(a, EvidenceArtifact)) for a in rows]
    unparsed = [a.id for a, ev in parsed if ev is None]
    valued = [ev for _, ev in parsed if ev is not None and ev.value is not None]
    nulls = [ev for _, ev in parsed if ev is not None and ev.value is None]

    out = CheckOutcome(check="evidence")
    if unparsed:
        out.status = "REJECTED"
        out.challenges.append(
            Challenge(
                category="evidence",
                severity="blocking",
                description=f"evidence payloads failed schema validation: {unparsed}",
                evidence_refs=unparsed,
            )
        )
        return out

    if not valued:
        out.status = "INSUFFICIENT"
        out.gaps.append(
            EvidenceGap(description="every evidence row has a null value", blocking=True, priority=9)
        )
        out.score_signals["evidence_quality"] = 0.0
        return out

    # A null among real values is a gap, not a failure -- never fabricate a 0.
    if nulls:
        out.gaps.append(
            EvidenceGap(description=f"{len(nulls)} evidence row(s) carry a null value", priority=4)
        )

    stale = [
        ev
        for ev in valued
        if (datetime.now(UTC) - ev.fetched_at.replace(tzinfo=ev.fetched_at.tzinfo or UTC)).total_seconds()
        > policy.MAX_FRESHNESS_HOURS * 3600
    ]
    thin = len(valued) < policy.MIN_OBSERVATION_ROWS

    quality = 1.0
    if nulls:
        quality -= 0.15
    if stale:
        quality -= 0.25
        out.challenges.append(
            Challenge(
                category="data_quality",
                severity="warning",
                description=f"{len(stale)} evidence row(s) older than {policy.MAX_FRESHNESS_HOURS}h",
            )
        )
    if thin:
        quality -= 0.2
        out.gaps.append(
            EvidenceGap(
                description=(
                    f"{len(valued)} usable evidence row(s), below the "
                    f"{policy.MIN_OBSERVATION_ROWS}-row floor"
                ),
                priority=6,
            )
        )
        out.status = "WEAK"

    out.score_signals["evidence_quality"] = max(0.0, round(quality, 3))
    return out


def check_provenance(artifacts: list[Artifact]) -> CheckOutcome:
    """Every factual/derived artifact traces to a source and a version (rule 6).

    ``Artifact.require_provenance()`` already enforces this at write time, so a
    violation here means something bypassed the store -- worth a blocking
    challenge rather than a quiet low score.
    """
    durable = [a for a in artifacts if a.classification in {"factual", "derived"}]
    if not durable:
        return CheckOutcome(check="provenance", status="NOT_APPLICABLE")

    out = CheckOutcome(check="provenance")
    offenders: list[str] = []
    for artifact in durable:
        try:
            artifact.require_provenance()
        except ValueError:
            offenders.append(artifact.id)

    if offenders:
        out.status = "REJECTED"
        out.challenges.append(
            Challenge(
                category="provenance",
                severity="blocking",
                description=f"artifacts missing evidence ids or a version stamp: {offenders}",
                evidence_refs=offenders,
            )
        )
        out.score_signals["provenance_completeness"] = 0.0
        return out

    out.score_signals["provenance_completeness"] = 1.0
    return out


def _holds_value(artifacts: list[Artifact], vf: Any) -> bool:
    """Some evidence row carries one of the named value's values on one of its dimensions."""
    wanted = {str(v).strip().lower() for v in vf.values}
    for artifact in artifacts:
        if artifact.artifact_type != "evidence":
            continue
        ev = _payload(artifact, EvidenceArtifact)
        if ev is None:
            continue
        for dim in vf.dimensions:
            held = ev.dimensions.get(dim)
            held_values = held if isinstance(held, list) else [held]
            if any(str(h).strip().lower() in wanted for h in held_values if h is not None):
                return True
    return False


def _filters_of(ev: EvidenceArtifact) -> str:
    """The query filters an evidence row was fetched under. Rows with the same labels
    but different filters measure different things: Suspender Boots orders by ad vs all
    orders by ad (live 2026-10-08 MS3-f528c2cde3 / MS3-6d88e04cc7: "product_orders on
    2026-10-01 reported as 15 and 16" spent three revisions the model could not fix)."""
    query = ev.source_query if isinstance(ev.source_query, dict) else {}
    filters = query.get("filters") or []
    return json.dumps(sorted(json.dumps(f, sort_keys=True, default=str) for f in filters))


def check_contradiction(artifacts: list[Artifact]) -> CheckOutcome:
    """Two evidence rows for the same metric and period must agree.

    Threshold and severity mapping follow
    ``agents/skeptic/validators/contradiction_validator.py``: a numeric
    disagreement above 5% between rows covering the same window is a
    ``source_conflict`` warning, which is in ``REVISE_CATEGORIES`` and so forces
    REVISE on its own.
    """
    rows = [(a, _payload(a, EvidenceArtifact)) for a in artifacts if a.artifact_type == "evidence"]
    series: dict[tuple[str, Any, Any, tuple], list[tuple[str, float]]] = {}
    for artifact, ev in rows:
        if ev is None or ev.value is None:
            continue
        key = (ev.metric_id, ev.period_start, ev.period_end, tuple(sorted(ev.dimensions.items())), _filters_of(ev))
        series.setdefault(key, []).append((artifact.id, float(ev.value)))

    out = CheckOutcome(check="contradiction")
    worst = 0.0
    for (metric_id, start, _end, _dims, _filters), points in series.items():
        if len(points) < 2:
            continue
        values = [v for _, v in points]
        low, high = min(values), max(values)
        base = abs(high) or 1.0
        disagreement = abs(high - low) / base
        if disagreement > policy.CONTRADICTION_TOLERANCE:
            worst = max(worst, disagreement)
            out.challenges.append(
                Challenge(
                    category="source",
                    severity="warning",
                    description=(
                        f"{metric_id} on {start.date()} reported as {low:.6g} and {high:.6g} "
                        f"({disagreement:.1%} apart)"
                    ),
                    evidence_refs=[aid for aid, _ in points],
                    contradiction_type="source_conflict",
                )
            )

    out.score_signals["cross_source_agreement"] = 0.4 if worst else 0.85
    return out


def check_causal(artifacts: list[Artifact]) -> CheckOutcome:
    """Causal claims carry a valid classification and enough refutation (rules 9/19).

    Only signals V3 can actually compute are emitted. ``temporal_validity`` and
    ``graph_plausibility`` are NOT -- swarm_v2 derives them from checks
    (``estimator.py:112-124``) that have no V3 equivalent, and emitting a
    fabricated 1.0 for them would silently inflate the causal trust profile,
    which weights them at 0.15 each.
    """
    rows = [a for a in artifacts if a.artifact_type == "causal"]
    if not rows:
        return CheckOutcome(check="causal", status="NOT_APPLICABLE")

    out = CheckOutcome(check="causal")
    refutation_scores: list[float] = []
    for artifact in rows:
        parsed = _payload(artifact, CausalArtifact)
        if parsed is None:
            out.status = "REJECTED"
            out.challenges.append(
                Challenge(
                    category="causal",
                    severity="blocking",
                    description=f"causal artifact {artifact.id} failed schema validation",
                    evidence_refs=[artifact.id],
                )
            )
            continue

        passed = [c for c in parsed.refutation_checks if c.get("passed")]
        refutation_scores.append(min(1.0, len(passed) / policy.MIN_REFUTATIONS))
        if parsed.evidence_classification == "CAUSALLY_SUPPORTED" and len(passed) < policy.MIN_REFUTATIONS:
            out.challenges.append(
                Challenge(
                    category="causal",
                    severity="warning",
                    description=(
                        f"{artifact.id} claims CAUSALLY_SUPPORTED on {len(passed)} passing "
                        f"refutation(s), below the {policy.MIN_REFUTATIONS} floor"
                    ),
                    evidence_refs=[artifact.id],
                )
            )

    if refutation_scores:
        out.score_signals["refutation_robustness"] = round(
            sum(refutation_scores) / len(refutation_scores), 3
        )
        out.score_signals["estimator_validity"] = 1.0
    return out


def check_prediction(artifacts: list[Artifact]) -> CheckOutcome:
    """Predictions carry model identity and an interval (rules 10/20).

    ``agents/skeptic/validators/forecast_validator.py`` treats a missing
    interval as a gap and an LLM-produced number as blocking; the same shape
    holds here.
    """
    rows = [a for a in artifacts if a.artifact_type == "prediction"]
    if not rows:
        return CheckOutcome(check="prediction", status="NOT_APPLICABLE")

    out = CheckOutcome(check="prediction")
    with_interval = 0
    leakage_unchecked: list[str] = []
    for artifact in rows:
        parsed = _payload(artifact, PredictionArtifact)
        if parsed is None:
            out.status = "REJECTED"
            out.challenges.append(
                Challenge(
                    category="forecast",
                    severity="blocking",
                    description=f"prediction artifact {artifact.id} failed schema validation",
                    evidence_refs=[artifact.id],
                )
            )
            continue
        if parsed.confidence_interval is not None:
            with_interval += 1
        else:
            out.gaps.append(
                EvidenceGap(
                    description=f"{parsed.model_id} produced a point forecast with no interval",
                    priority=6,
                )
            )
        if not parsed.feature_leakage_checked:
            leakage_unchecked.append(artifact.id)

    if leakage_unchecked:
        out.challenges.append(
            Challenge(
                category="model",
                severity="warning",
                description=f"predictions with no feature-leakage check: {leakage_unchecked}",
                evidence_refs=leakage_unchecked,
            )
        )

    out.score_signals["model_applicability"] = 1.0 if not leakage_unchecked else 0.5
    out.score_signals["interval_quality"] = round(with_interval / len(rows), 3)
    return out


def _merged_spans(periods: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Collapse evidence periods into the maximal contiguous spans they cover.

    Coverage of a requested window is a property of the *union*, not of any one
    row. A day-grained fetch of 10-03..10-06 writes one artifact per day, each
    spanning a single day; requiring one row to contain a multi-day window would
    call that complete evidence a gap and send a correct answer back for
    revision. Overlapping and *adjacent* days merge; a genuine hole (10-03 and
    10-05 with no 10-04) leaves two spans and correctly fails containment.
    """
    if not periods:
        return []
    ordered = sorted(periods)
    merged: list[tuple[date, date]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + timedelta(days=1):
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _spans_contain(
    spans: list[tuple[date, date]], win_start: date, win_end: date
) -> bool:
    """True when some merged span wholly covers ``[win_start, win_end]``."""
    if win_end < win_start:
        win_start, win_end = win_end, win_start
    return any(start <= win_start and win_end <= end for start, end in spans)


def check_scope_coverage(
    artifacts: list[Artifact],
    scope: Any,
    catalogue: Any = None,
    not_values: Any = (),
    cited: Any = (),
    as_of: datetime | None = None,
) -> CheckOutcome:
    """Executed evidence must cover the breakdowns and named values the query demanded.

    The reconciliation gate for the silent-drop failure (live "by source"
    trace): each requested breakdown is a **candidate set** of catalogue
    dimensions sharing that grain language (``RequiredScope.breakdowns``, built
    in the runner). Coverage needs at least one candidate grouped on an evidence
    row — so grouping by a valid sibling (``lt_channel`` where the resolver named
    ``channel``) passes, while grouping by nothing still fails. A miss is a
    **blocking gap → REVISE**, not a REJECT: the answer may be right in kind but
    does not cover what was asked, so the model gets one chance to redo it (and,
    if the breakdown is genuinely unsupported, to say so) rather than shipping a
    different-question answer as ``completed``.

    Temporal grain coverage: if the query requests a coarser grain (e.g., quarter)
    but evidence exists at a finer grain (e.g., month), the finer grain satisfies
    the request via valid aggregation (sum for additive metrics).

    NOT_APPLICABLE when the query demanded no resolvable breakdown, or when the
    mission produced no evidence at all (``check_evidence`` owns that case — a
    coverage gap on top would just double-count the same failure)."""
    # Each element is a candidate set; tolerate a bare dimension id (legacy /
    # defensive) by treating it as a one-candidate set rather than iterating it
    # into characters.
    breakdowns = [
        frozenset([cs]) if isinstance(cs, str) else frozenset(cs)
        for cs in (getattr(scope, "breakdowns", ()) or ())
    ]
    # A value hint is a candidate, not a command: an exact data match on an ordinary word ("compared to
    # OTHER days" -> payment_method = other) is not scope. The answer names such words in not_values and
    # they stop being required (live thread_e75c2615: six forced revisions toward a filter nobody asked for).
    waived = {str(w).strip().lower() for w in (not_values or ()) if str(w).strip()}
    value_filters = tuple(
        vf for vf in (getattr(scope, "value_filters", ()) or ()) if str(vf.term).strip().lower() not in waived
    )
    requested_grain = getattr(scope, "temporal_grain", None)
    stated_date = dict(getattr(scope, "question_axes", ()) or ()).get("date")
    windows = tuple(getattr(scope, "windows", ()) or ())
    if not breakdowns and not value_filters and not requested_grain and not stated_date and not windows:
        return CheckOutcome(check="scope_coverage", status="NOT_APPLICABLE")
    evidence = [a for a in artifacts if a.artifact_type == "evidence"]
    if not evidence:
        return CheckOutcome(check="scope_coverage", status="NOT_APPLICABLE")

    grouped: set[str] = set()
    filtered: set[str] = set()
    available_grains: set[str] = set()
    answer_metric_ids: set[str] = set()
    # Date spans the evidence actually covers, for the requested-window
    # reconciliation below.
    evidence_periods: list[tuple[date, date]] = []
    # Per metric: the dimensions its evidence is grouped or filtered by, and
    # whether the answer cites it.
    scoped_by_metric: dict[str, set[str]] = {}
    cited_ids = {str(c) for c in (cited or ())}
    cited_metrics: set[str] = set()
    for artifact in evidence:
        parsed = _payload(artifact, EvidenceArtifact)
        own: set[str] = set()
        if parsed is not None:
            own.update(parsed.dimensions.keys())
            grouped.update(parsed.dimensions.keys())
            if parsed.metric_id:
                answer_metric_ids.add(parsed.metric_id)
            if parsed.grain and parsed.grain != "none":
                available_grains.add(parsed.grain)
            try:
                evidence_periods.append(
                    (
                        parsed.period_start.date(),
                        parsed.period_end.date(),
                    )
                )
            except AttributeError:
                pass
        # A named value is covered by *filtering* just as well as grouping (a
        # brand/source scope is applied as a Cube filter, which lands in
        # provenance, not in the row's grouped dimensions). Without this a
        # correctly-filtered answer — incl. the auto-applied workspace brand —
        # was scored as uncovered and failed closed (live MS3-0648c0208b:
        # brand_id filtered, dimensions={}, mission killed with a full answer).
        for applied in artifact.provenance.source_metadata.get("filters_applied") or []:
            if isinstance(applied, dict) and (dim := applied.get("dimension")):
                filtered.add(str(dim))
                own.add(str(dim))
        # a conformed sibling is the same slice (the tools answer "platform" with finance_channel on a P&L
        # metric, "ad_platform" with acquisition_platform on customers): it covers its whole family
        family_members = getattr(catalogue, "family_members", None)
        if family_members is not None:
            own = {m for d in own for m in family_members(d)}
            grouped.update(m for d in list(grouped) for m in family_members(d))
            filtered.update(m for d in list(filtered) for m in family_members(d))
        if parsed is not None and parsed.metric_id:
            scoped_by_metric.setdefault(parsed.metric_id, set()).update(own)
            if artifact.id in cited_ids:
                cited_metrics.add(parsed.metric_id)

    def _answer_metric_can_group(dims: frozenset[str]) -> bool:
        """Catalogue-driven: does a metric ACTUALLY USED in the answer support one
        of these breakdown dims? If yes and it wasn't grouped, that's the silent-
        drop bug (blocking). If the chosen metric can't carry the dim, no revision
        of THIS answer can — other metrics that list the dim measure a different
        thing (net sales has no hour/session axis; hourly lives on ad metrics,
        session_day_of_week on web metrics), so switching would answer a different
        question. Unknown/empty catalogue -> assume yes (fail-closed: keep guard)."""
        supported_for = getattr(catalogue, "supported_dimensions_for", None)
        if not getattr(catalogue, "metrics", None) or supported_for is None or not answer_metric_ids:
            return True
        for mid in answer_metric_ids:
            if dims & set(supported_for(mid)):
                return True
        return False

    gaps: list[EvidenceGap] = []
    for candidates in breakdowns:
        if candidates & grouped:
            continue
        options = " or ".join(sorted(candidates))
        # A breakdown NO queryable metric supports is genuinely unanswerable: no
        # revision can ever group by it, so a blocking gap only loops the mission
        # to VALIDATION_REVISIONS_EXHAUSTED on an otherwise-correct answer (live
        # MS3-dc65868b71: "net sales by hour_of_day/session_day_of_week" — the
        # agent correctly said the metric can't, but the guard killed it). Keep it
        # blocking only when some metric CAN carry it (the silent-drop case).
        answerable = _answer_metric_can_group(candidates)
        gaps.append(
            EvidenceGap(
                description=(
                    f"the question asked for a breakdown by {options}, but the answer's "
                    f"evidence is not grouped by any of them — "
                    + (
                        "re-run grouped by one of those dimensions"
                        if answerable
                        else "no available metric supports that breakdown; state that plainly"
                    )
                ),
                blocking=answerable,
                priority=8 if answerable else 3,
            )
        )
    # A named value (live: "orders from whatsapp") must actually constrain the
    # evidence — filtered or grouped by one of the dimensions the data records
    # it in. Otherwise the answer is a total that ignores what was asked.
    supported_for = getattr(catalogue, "supported_dimensions_for", None)
    for vf in value_filters:
        # Several values of one kind ("across Meta, Google, organic, WhatsApp") are the
        # rows being compared, not a scope: the whole and its other parts belong in the
        # answer, so each value only has to appear in the evidence (live 2026-10-09
        # MS3-c0f21457ca: every metric was demanded filtered to Meta, partial).
        # Weighed against the whole ("is Meta responsible", read by the understanding): the total and the part both
        # belong in the answer — each value only has to appear (regression 2026-10-09 Q28: the gate demanded every
        # figure filtered to Meta and sent a correct attribution answer back).
        compared = getattr(scope, "values_weighed_against_whole", False) or any(
            other is not vf and set(other.dimensions) & set(vf.dimensions) for other in value_filters
        )
        if compared and _holds_value(artifacts, vf):
            continue
        if (grouped | filtered) & set(vf.dimensions):
            # Covered somewhere — but every metric the answer reports that CAN
            # carry the value must carry it. Live 2026-10-05 MS3-c97c9fea15: a
            # "Meta ads report" filtered orders to Meta and reported spend,
            # impressions and clicks for Meta + Google; the mission-wide union
            # passed it.
            reported = cited_metrics or set(scoped_by_metric)
            unscoped = sorted(
                mid
                for mid in reported
                if not (scoped_by_metric.get(mid, set()) & set(vf.dimensions))
                and (
                    supported_for is None
                    or not getattr(catalogue, "metrics", None)
                    or set(vf.dimensions) & set(supported_for(mid))
                )
            )
            if unscoped:
                gaps.append(
                    EvidenceGap(
                        description=(
                            f"the question names '{vf.term}' ({' / '.join(sorted(vf.dimensions))} = "
                            f"{', '.join(vf.values)}), but {', '.join(unscoped)} "
                            f"{'was' if len(unscoped) == 1 else 'were'} fetched without that filter, so "
                            f"those numbers cover every value, not '{vf.term}' — re-query them filtered "
                            f"to it (pass the filter in dimensions), or drop them"
                        ),
                        blocking=True,
                        priority=8,
                    )
                )
            continue
        # A value the data records under several unrelated dimensions ("other": a channel, a platform and a
        # payment method) does not say which one the user meant — usually none ("other available cost
        # components", live 2026-10-09: four forced revisions on an exact P&L bridge, shipped partial). It stays a
        # hint; a value of one family ("meta": every platform member) is a real scope and still blocks.
        family_head = getattr(catalogue, "family_head", None)
        families_known = family_head is not None and bool(getattr(catalogue, "dimension_families", None))
        meanings = {family_head(d) for d in vf.dimensions} if families_known else set()
        if len(meanings) > 1:
            gaps.append(
                EvidenceGap(
                    description=(
                        f"the question uses '{vf.term}', which the data records under several unrelated "
                        f"dimensions ({' / '.join(sorted(meanings))}); if the user meant one of those values, "
                        f"filter to it and say which — otherwise it is an ordinary word"
                    ),
                    blocking=False,
                    priority=3,
                )
            )
            continue
        gaps.append(
            EvidenceGap(
                description=(
                    f"the question names '{vf.term}', which the data records as "
                    f"{' / '.join(sorted(vf.dimensions))} = {', '.join(vf.values)}, but the "
                    f"answer's evidence is not filtered by it — re-run filtered to those "
                    f"values with a metric that supports that dimension, or state plainly "
                    f"that no available metric supports it; if the question uses "
                    f"'{vf.term}' as an ordinary word rather than that value, or if the "
                    f"dimension does not match the user's intended entity (e.g. ad_name vs product), "
                    f"list it in not_values instead"
                ),
                blocking=True,
                priority=8,
            )
        )
    # Date basis (semantic v2): the user's words set the date axis ("on the P&L" -> finance / event date;
    # "orders placed" -> order) and the answer used a metric whose catalogue twin is on the other axis — the
    # number is right for a different question. Catalogue-driven (date_basis / date_twin from the gateway).
    wanted = scope.axis("date") if hasattr(scope, "axis") else None
    basis_for = getattr(catalogue, "date_basis_for", None)
    if wanted and basis_for is not None:
        for mid in sorted(answer_metric_ids):
            basis, twin = basis_for(mid)
            if basis and twin and basis != wanted:
                gaps.append(
                    EvidenceGap(
                        description=(
                            f"the question asks for the {wanted} date basis, but '{mid}' is on the {basis} "
                            f"basis — re-run with '{twin}' (same measure on the {wanted} basis)"
                        ),
                        blocking=True,
                        priority=8,
                    )
                )
    # Temporal grain coverage: only a real time-series mismatch is a gap. Evidence
    # exists at a bucket grain (available_grains non-empty) but none satisfies the
    # request. A period total (grain="none", so available_grains empty) is the
    # correct answer to a single-window question ("last week vs this month") and
    # must NOT be flagged — else the revision loop is unsatisfiable (live
    # MS3-a794c66a66: correct week/month totals killed as "unknown grain").
    if requested_grain and requested_grain != "none" and available_grains:
        grain_satisfied = any(_grain_satisfies(requested_grain, ag) for ag in available_grains)
        if not grain_satisfied:
            gaps.append(
                EvidenceGap(
                    description=(
                        f"the question asked for {requested_grain} grain, but the answer's "
                        f"evidence is at {', '.join(sorted(available_grains)) or 'unknown'} grain — "
                        f"re-run with a metric that supports {requested_grain} or a finer grain "
                        f"that can be aggregated up, or state plainly that no available metric supports it"
                    ),
                    blocking=True,
                    priority=8,
                )
            )

    # Requested time windows. A comparison question names two periods and both
    # must appear in the evidence. Before this gate existed, nothing reconciled
    # them: live 2026-10-06 (MS3-167d9f4838) asked to compare "the last 3 days
    # versus today", the resolver dropped the second window, the mission fetched
    # and analysed only 2026-10-03..10-05, and shipped that half as
    # ``completed`` with a clarifying question about the rest.
    #
    # Containment, not equality: one fetch spanning both periods ("last 7 days
    # for a vs-b question") satisfies both, and a daily series covering a range
    # satisfies the range. Per mission rather than per metric — requiring every
    # metric to cover both windows would flag a correct answer that compares
    # windows on the metrics it could and states the rest as unavailable.
    #
    # A period compared with a period to date is covered by its same span (the
    # executor fetches exactly that); asking for the rest of it sent the model back
    # to fetch the whole week and compare 7 days with 4 (live 2026-10-08).
    dated = [
        (w.start, w.end) for w in windows
        if isinstance(getattr(w, "start", None), date) and isinstance(getattr(w, "end", None), date)
    ]
    for win_start, win_end in dated:
        if as_of is not None:
            for other in dated:
                if (span := same_span((win_start, win_end), other, as_of)) is not None:
                    win_start, win_end = span
                    break
        if _spans_contain(_merged_spans(evidence_periods), win_start, win_end):
            continue
        # A window running into today is covered by its complete days: a diagnosis leaves the
        # running day out on purpose (live 2026-10-09: "this week vs last week" revised for 10-09),
        # and the period compared with it then drops its counterpart day, its last.
        today = in_progress_day(as_of) if as_of is not None else None
        if today is not None and win_start < today <= win_end:
            covered_through = today - timedelta(days=1)
        elif today is not None and win_start < win_end and any(s < today <= e for s, e in dated):
            covered_through = win_end - timedelta(days=1)
        else:
            covered_through = None
        if covered_through is not None and _spans_contain(_merged_spans(evidence_periods), win_start, covered_through):
            continue
        gaps.append(
            EvidenceGap(
                description=(
                    f"the question asks about {win_start}..{win_end} as well, but no evidence "
                    f"in this mission covers that period — a comparison question needs both "
                    f"periods fetched before it can be answered. Fetch it (one query_metrics "
                    f"for EVERY metric the answer shows for that period, over that window) and "
                    f"replace all of that period's figures — headline, table row and change — "
                    f"with the new ones, or state plainly that the period is unavailable. Never "
                    f"mix a newly fetched figure with figures of another window in one row"
                ),
                blocking=True,
                priority=8,
            )
        )

    if not gaps:
        return CheckOutcome(check="scope_coverage")
    # Only a blocking gap makes coverage INSUFFICIENT (drives REVISE). Informational
    # gaps (e.g. an unanswerable breakdown) still surface in the reason but must not
    # loop the mission to exhaustion on a correct "unsupported" answer.
    status = "INSUFFICIENT" if any(g.blocking for g in gaps) else "OK"
    return CheckOutcome(check="scope_coverage", status=status, gaps=gaps)


# A plan is observability; a signal is context from the hourly snapshots
# (services/insights.py), not this mission's claim.
_NON_CLAIM_ARTIFACT_TYPES = frozenset({"plan", "signal"})


def run_checks(
    deps: SelericDeps, result: MissionResult | None = None
) -> tuple[list[CheckOutcome], list[EvidenceGap], ClaimType]:
    """Every V3 check over one mission's artifacts, plus the collected gaps.

    ``result`` (the answer under review) enables the answer-grounding check;
    without it only the artifact checks run.

    Gap priority is *not* recomputed the way
    ``agents/skeptic/evidence_gaps.py::collect_gaps`` does (EIG/impact/cost).
    ``decide_verdict`` reads only ``gap.blocking``, never ``gap.priority``, so
    the recompute changes nothing about the verdict -- porting it would add a
    scoring model with no consumer.
    """
    from seleric_swarm.agent.validation.grounding import check_answer_grounding

    # A plan is observability, not a claim: counting it as a derived artifact
    # would fail every planned mission that (correctly) fetched no data.
    artifacts = [
        a
        for a in deps.artifact_store.list_for_mission(deps.mission_id)
        if a.artifact_type not in _NON_CLAIM_ARTIFACT_TYPES
    ]
    outcomes = [
        check_evidence(artifacts),
        check_provenance(artifacts),
        check_contradiction(artifacts),
        check_causal(artifacts),
        check_prediction(artifacts),
        check_scope_coverage(
            artifacts,
            getattr(deps, "required_scope", None),
            getattr(deps, "catalogue", None),
            not_values=getattr(result, "not_values", None) or (),
            cited=getattr(result, "evidence_ids", None) or (),
            as_of=getattr(deps, "as_of", None),
        ),
        check_answer_grounding(artifacts, result),
    ]
    live = [oc for oc in outcomes if oc.status != "NOT_APPLICABLE"]
    gaps = [gap for oc in live for gap in oc.gaps]
    return live, gaps, claim_type_for(artifacts)

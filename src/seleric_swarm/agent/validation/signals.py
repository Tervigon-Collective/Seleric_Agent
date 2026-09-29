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
from datetime import UTC, datetime
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
        key = (ev.metric_id, ev.period_start, ev.period_end, tuple(sorted(ev.dimensions.items())))
        series.setdefault(key, []).append((artifact.id, float(ev.value)))

    out = CheckOutcome(check="contradiction")
    worst = 0.0
    for (metric_id, start, _end, _dims), points in series.items():
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


def check_scope_coverage(artifacts: list[Artifact], scope: Any, catalogue: Any = None) -> CheckOutcome:
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
    value_filters = tuple(getattr(scope, "value_filters", ()) or ())
    requested_grain = getattr(scope, "temporal_grain", None)
    if not breakdowns and not value_filters and not requested_grain:
        return CheckOutcome(check="scope_coverage", status="NOT_APPLICABLE")
    evidence = [a for a in artifacts if a.artifact_type == "evidence"]
    if not evidence:
        return CheckOutcome(check="scope_coverage", status="NOT_APPLICABLE")

    grouped: set[str] = set()
    filtered: set[str] = set()
    available_grains: set[str] = set()
    answer_metric_ids: set[str] = set()
    for artifact in evidence:
        parsed = _payload(artifact, EvidenceArtifact)
        if parsed is not None:
            grouped.update(parsed.dimensions.keys())
            if parsed.metric_id:
                answer_metric_ids.add(parsed.metric_id)
            if parsed.grain and parsed.grain != "none":
                available_grains.add(parsed.grain)
        # A named value is covered by *filtering* just as well as grouping (a
        # brand/source scope is applied as a Cube filter, which lands in
        # provenance, not in the row's grouped dimensions). Without this a
        # correctly-filtered answer — incl. the auto-applied workspace brand —
        # was scored as uncovered and failed closed (live MS3-0648c0208b:
        # brand_id filtered, dimensions={}, mission killed with a full answer).
        for applied in artifact.provenance.source_metadata.get("filters_applied") or []:
            if isinstance(applied, dict) and (dim := applied.get("dimension")):
                filtered.add(str(dim))

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
    for vf in value_filters:
        if (grouped | filtered) & set(vf.dimensions):
            continue
        gaps.append(
            EvidenceGap(
                description=(
                    f"the question names '{vf.term}', which the data records as "
                    f"{' / '.join(sorted(vf.dimensions))} = {', '.join(vf.values)}, but the "
                    f"answer's evidence is not filtered by it — re-run filtered to those "
                    f"values with a metric that supports that dimension, or state plainly "
                    f"that no available metric supports it"
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

    if not gaps:
        return CheckOutcome(check="scope_coverage")
    # Only a blocking gap makes coverage INSUFFICIENT (drives REVISE). Informational
    # gaps (e.g. an unanswerable breakdown) still surface in the reason but must not
    # loop the mission to exhaustion on a correct "unsupported" answer.
    status = "INSUFFICIENT" if any(g.blocking for g in gaps) else "OK"
    return CheckOutcome(check="scope_coverage", status=status, gaps=gaps)


_NON_CLAIM_ARTIFACT_TYPES = frozenset({"plan"})


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
            artifacts, getattr(deps, "required_scope", None), getattr(deps, "catalogue", None)
        ),
        check_answer_grounding(artifacts, result),
    ]
    live = [oc for oc in outcomes if oc.status != "NOT_APPLICABLE"]
    gaps = [gap for oc in live for gap in oc.gaps]
    return live, gaps, claim_type_for(artifacts)

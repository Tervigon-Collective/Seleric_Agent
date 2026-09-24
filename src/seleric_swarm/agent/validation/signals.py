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

if TYPE_CHECKING:
    from seleric_swarm.agent.dependencies import SelericDeps
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


def check_scope_coverage(artifacts: list[Artifact], scope: Any) -> CheckOutcome:
    """Executed evidence must cover the breakdowns the query demanded.

    The reconciliation gate for the silent-drop failure (live "by source"
    trace): a requested breakdown that resolved to a real catalogue dimension
    (``RequiredScope.breakdowns``, built in the runner) must appear as a
    grouping key on at least one evidence row. A missing one is a **blocking
    gap → REVISE**, not a REJECT: the answer may be right in kind but does not
    cover what was asked, so the model gets one chance to redo it (and, if the
    breakdown is genuinely unsupported, to say so) rather than shipping a
    different-question answer as ``completed``.

    NOT_APPLICABLE when the query demanded no resolvable breakdown, or when the
    mission produced no evidence at all (``check_evidence`` owns that case — a
    coverage gap on top would just double-count the same failure)."""
    breakdowns = frozenset(getattr(scope, "breakdowns", ()) or ())
    if not breakdowns:
        return CheckOutcome(check="scope_coverage", status="NOT_APPLICABLE")
    evidence = [a for a in artifacts if a.artifact_type == "evidence"]
    if not evidence:
        return CheckOutcome(check="scope_coverage", status="NOT_APPLICABLE")

    grouped: set[str] = set()
    for artifact in evidence:
        parsed = _payload(artifact, EvidenceArtifact)
        if parsed is not None:
            grouped.update(parsed.dimensions.keys())

    missing = sorted(breakdowns - grouped)
    if not missing:
        return CheckOutcome(check="scope_coverage")
    return CheckOutcome(
        check="scope_coverage",
        status="INSUFFICIENT",
        gaps=[
            EvidenceGap(
                description=(
                    f"the question asked for a breakdown by {missing}, but the answer's "
                    f"evidence is not grouped by it — re-run grouped by that dimension, "
                    f"or state plainly that no available metric supports that breakdown"
                ),
                blocking=True,
                priority=8,
            )
        ],
    )


_NON_CLAIM_ARTIFACT_TYPES = frozenset({"plan"})


def run_checks(deps: SelericDeps) -> tuple[list[CheckOutcome], list[EvidenceGap], ClaimType]:
    """Every V3 check over one mission's artifacts, plus the collected gaps.

    Gap priority is *not* recomputed the way
    ``agents/skeptic/evidence_gaps.py::collect_gaps`` does (EIG/impact/cost).
    ``decide_verdict`` reads only ``gap.blocking``, never ``gap.priority``, so
    the recompute changes nothing about the verdict -- porting it would add a
    scoring model with no consumer.
    """
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
        check_scope_coverage(artifacts, getattr(deps, "required_scope", None)),
    ]
    live = [oc for oc in outcomes if oc.status != "NOT_APPLICABLE"]
    gaps = [gap for oc in live for gap in oc.gaps]
    return live, gaps, claim_type_for(artifacts)

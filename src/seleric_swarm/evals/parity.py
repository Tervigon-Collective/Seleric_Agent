"""Behavioral parity: swarm_v2 specialists vs. the V3 toolsets (Profile C, Sprint 3).

Profile C is the highest-behavioral-risk profile in the migration, and until
this it had no behavioral parity criterion at all — its exit criteria were
regression tests for known bugs plus a schema-completeness check that a
required Pydantic field satisfies trivially. This is the missing gate
(``03_PROFILE_CAPABILITIES.md`` §9 criterion (d)7).

Two deliberate design choices
-----------------------------

**1. Same evidence in, both paths.** The toolsets are driven directly rather
than through an LLM agent loop. That makes the comparison deterministic and
attributable: a divergence here is a difference in *capability*, full stop.
Whether the agent picks the right tool is a separate question and belongs to
Sprint 4's eval/cost gate — conflating the two would make every failure
ambiguous ("did the analytics change, or did the model just call something
else?").

**2. Structural equivalence, not exact floats.** Compared: the set of
``(metric_id, direction)`` anomalies, evidence classifications, hypothesis
statements. Reported but not asserted: z-scores, effect estimates, interval
bounds. Exact numeric parity would fail on changes that are *intended* — most
importantly, ``detect_anomalies`` now derives its baseline from evidence rather
than from ``BusinessStateService`` history, which is non-negotiable rule 5
working as designed, not a regression.

A divergence is not automatically a failure; an **unexplained** one is. Every
known-intentional difference is registered in ``EXPECTED_DIVERGENCES`` with the
reason, so the report distinguishes "we changed this on purpose and here is
why" from "we do not know why this moved".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from seleric_swarm.agent.artifacts import CausalArtifact

DivergenceKind = Literal["anomaly_set", "classification", "hypothesis_set", "numeric"]

# Differences this migration made on purpose. Keyed by kind, each with the
# reason it is expected -- so a reviewer reads a rationale, not a diff.
EXPECTED_DIVERGENCES: dict[str, str] = {
    "baseline_source": (
        "detect_anomalies takes its baseline from the supplied evidence series; "
        "swarm_v2's RobustZScoreDetector fetched its own history via "
        "BusinessStateService.get_metric_state(). Rule 5 forbids a tool fetching "
        "mid-calculation, so the numbers can differ where the two windows differ."
    ),
    "grain_refusal": (
        "V3 refuses an evidence set whose grain contradicts its period span "
        "(EVIDENCE_GRAIN_MISMATCH, A1.2). swarm_v2 normalized a multi-day sum to a "
        "per-day average instead. Where swarm_v2 produced a normalized anomaly, V3 "
        "produces no finding at all -- this is docs/BUG_SHEET.md #14's fix."
    ),
    "search_breadth": (
        "Causal widening is the caller-chosen search_breadth ladder (A1.1) rather "
        "than swarm_v2's implicit remediation_round counter, so a single V3 call "
        "corresponds to one rung rather than to a whole remediation loop."
    ),
    "no_metadata_ceiling": (
        "swarm_v2 capped a no-observations run at PLAUSIBLE_CAUSAL via "
        "cap_metadata_confidence; V3's vocabulary has no PLAUSIBLE_CAUSAL and "
        "classifies a frameless run as ASSOCIATION. See the A1.4 scope gap."
    ),
}


@dataclass(frozen=True)
class AnomalySignature:
    """What must match. Deliberately excludes the magnitude."""

    metric_id: str
    direction: str

    def __str__(self) -> str:
        return f"{self.metric_id}:{self.direction}"


@dataclass
class Divergence:
    kind: DivergenceKind
    detail: str
    expected_reason: str | None = None

    @property
    def explained(self) -> bool:
        return self.expected_reason is not None


@dataclass
class ParityReport:
    mission: str
    legacy_anomalies: set[AnomalySignature] = field(default_factory=set)
    v3_anomalies: set[AnomalySignature] = field(default_factory=set)
    legacy_classifications: list[str] = field(default_factory=list)
    v3_classifications: list[str] = field(default_factory=list)
    legacy_hypotheses: set[str] = field(default_factory=set)
    v3_hypotheses: set[str] = field(default_factory=set)
    divergences: list[Divergence] = field(default_factory=list)
    numeric: list[str] = field(default_factory=list)

    @property
    def unexplained(self) -> list[Divergence]:
        return [d for d in self.divergences if not d.explained]

    @property
    def passed(self) -> bool:
        """Structural equivalence, or every divergence carrying a reason."""
        return not self.unexplained

    def render(self) -> str:
        lines = [f"## {self.mission}", ""]
        lines.append(f"anomalies  legacy={sorted(map(str, self.legacy_anomalies))}")
        lines.append(f"           v3    ={sorted(map(str, self.v3_anomalies))}")
        if self.legacy_classifications or self.v3_classifications:
            lines.append(f"classification legacy={self.legacy_classifications} v3={self.v3_classifications}")
        if self.legacy_hypotheses or self.v3_hypotheses:
            lines.append(f"hypotheses legacy={len(self.legacy_hypotheses)} v3={len(self.v3_hypotheses)}")
        for note in self.numeric:
            lines.append(f"  [numeric, reported not asserted] {note}")
        for divergence in self.divergences:
            marker = "explained" if divergence.explained else "UNEXPLAINED"
            lines.append(f"  [{marker}] {divergence.kind}: {divergence.detail}")
            if divergence.expected_reason:
                lines.append(f"      reason: {divergence.expected_reason}")
        lines.append("")
        lines.append("PASS" if self.passed else "FAIL — unexplained divergence")
        return "\n".join(lines)


def compare(
    mission: str,
    *,
    legacy_anomalies: set[AnomalySignature],
    v3_anomalies: set[AnomalySignature],
    legacy_classifications: list[str] | None = None,
    v3_classifications: list[str] | None = None,
    legacy_hypotheses: set[str] | None = None,
    v3_hypotheses: set[str] | None = None,
    expected: dict[AnomalySignature | str, str] | None = None,
    numeric: list[str] | None = None,
) -> ParityReport:
    """Structural diff of one mission across both paths.

    ``expected`` maps a specific signature (or classification string) to the
    ``EXPECTED_DIVERGENCES`` key explaining it. Anything not listed there and not
    matching comes back unexplained, which fails the report.
    """
    expected = expected or {}
    report = ParityReport(
        mission=mission,
        legacy_anomalies=legacy_anomalies,
        v3_anomalies=v3_anomalies,
        legacy_classifications=legacy_classifications or [],
        v3_classifications=v3_classifications or [],
        legacy_hypotheses=legacy_hypotheses or set(),
        v3_hypotheses=v3_hypotheses or set(),
        numeric=numeric or [],
    )

    for missing in sorted(legacy_anomalies - v3_anomalies, key=str):
        report.divergences.append(
            Divergence(
                kind="anomaly_set",
                detail=f"legacy flagged {missing}, v3 did not",
                expected_reason=EXPECTED_DIVERGENCES.get(str(expected.get(missing, ""))),
            )
        )
    for extra in sorted(v3_anomalies - legacy_anomalies, key=str):
        report.divergences.append(
            Divergence(
                kind="anomaly_set",
                detail=f"v3 flagged {extra}, legacy did not",
                expected_reason=EXPECTED_DIVERGENCES.get(str(expected.get(extra, ""))),
            )
        )

    if (legacy_classifications or []) != (v3_classifications or []):
        report.divergences.append(
            Divergence(
                kind="classification",
                detail=f"legacy={legacy_classifications} v3={v3_classifications}",
                expected_reason=EXPECTED_DIVERGENCES.get(str(expected.get("classification", ""))),
            )
        )

    legacy_h, v3_h = legacy_hypotheses or set(), v3_hypotheses or set()
    if legacy_h != v3_h:
        report.divergences.append(
            Divergence(
                kind="hypothesis_set",
                detail=f"legacy-only={sorted(legacy_h - v3_h)} v3-only={sorted(v3_h - legacy_h)}",
                expected_reason=EXPECTED_DIVERGENCES.get(str(expected.get("hypotheses", ""))),
            )
        )

    return report


def anomalies_from_findings(findings: list[dict[str, Any]]) -> set[AnomalySignature]:
    """V3 ``Finding`` payloads -> comparable signatures.

    Direction is derived from the sign of the deviation rather than read from a
    field, because swarm_v2's ``AnomalyFinding.direction`` and V3's Finding
    metrics are shaped differently — the *claim* ("this metric moved down") is
    what has to match, not the representation.
    """
    out: set[AnomalySignature] = set()
    for payload in findings:
        if payload.get("finding_type") != "anomaly":
            continue
        metrics = payload.get("metrics") or {}
        observed, expected_value = metrics.get("observed"), metrics.get("expected")
        if observed is None or expected_value is None:
            continue
        direction = "up" if observed > expected_value else ("down" if observed < expected_value else "flat")
        statement = str(payload.get("statement", ""))
        metric_id = statement.split(" ", 1)[0] if statement else "unknown"
        out.add(AnomalySignature(metric_id=metric_id, direction=direction))
    return out


def anomalies_from_blackboard(rows: list[dict[str, Any]]) -> set[AnomalySignature]:
    """swarm_v2 ``Anomaly`` artifacts -> the same signature shape."""
    out: set[AnomalySignature] = set()
    for row in rows:
        if row.get("artifact_type") != "anomaly":
            continue
        observed = row.get("observed")
        band = row.get("expected_range") or []
        if observed is None or len(band) != 2:
            continue
        midpoint = (float(band[0]) + float(band[1])) / 2
        direction = "up" if observed > midpoint else ("down" if observed < midpoint else "flat")
        out.add(AnomalySignature(metric_id=str(row.get("metric_id")), direction=direction))
    return out


def classifications_of(artifacts: list[CausalArtifact]) -> list[str]:
    return sorted(a.evidence_classification for a in artifacts)

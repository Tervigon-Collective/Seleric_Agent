"""Answer grounding: the numbers in ``final_response`` must come from evidence.

Two questions, answered from the mission's own artifacts only:

1. **Per-period coverage** (drives REVISE). Every period the answer cites
   evidence for must show up in the answer as at least one number drawn from
   that period's evidence. The live failure this catches: evidence for both
   compared months existed, the answer printed "No data available" for one.
2. **Ungrounded numbers** (reported, never a verdict on their own). Numbers in
   the answer that match no evidence value or finding metric are listed in the
   reason so the revision can fix or drop them.

Numbers are found by scanning characters (digits, grouping commas, one decimal
point) — no patterns, no unit words. A displayed number matches a source value
when it equals the value at some power-of-ten display scale (percent, thousand,
lakh, million, crore) within the rounding its own printed precision implies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
from seleric_swarm.agent.validation.signals import CheckOutcome, EvidenceGap

if TYPE_CHECKING:
    from seleric_swarm.agent.output import MissionResult
    from seleric_swarm.conversations.contracts import Artifact

# Display scales a number may be printed at: ratio as percent (10^-2) up to crore (10^7).
_SCALE_EXPONENTS = (-2, 0, 3, 5, 6, 7)
# Relative slack on top of print-precision rounding (currency rounding, FX display).
_RELATIVE_TOLERANCE = 0.005


@dataclass(frozen=True)
class AnswerNumber:
    text: str
    value: float
    decimals: int


def scan_numbers(text: str) -> list[AnswerNumber]:
    """Numbers in ``text``: digit runs with grouping commas and at most one
    decimal point. A run glued to a preceding letter (``Q3``, ``SKU12``) is an
    identifier, not a quantity, and is skipped."""
    out: list[AnswerNumber] = []
    i, n = 0, len(text)
    while i < n:
        if not text[i].isdigit():
            i += 1
            continue
        if i > 0 and (text[i - 1].isalpha() or text[i - 1] == "_"):
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            continue
        start = i
        digits: list[str] = []
        decimals = -1
        while i < n:
            ch = text[i]
            nxt = text[i + 1] if i + 1 < n else ""
            if ch.isdigit():
                digits.append(ch)
                if decimals >= 0:
                    decimals += 1
            elif ch == "," and nxt.isdigit() and decimals < 0:
                pass
            elif ch == "." and nxt.isdigit() and decimals < 0:
                digits.append(".")
                decimals = 0
            else:
                break
            i += 1
        raw = text[start:i]
        try:
            out.append(AnswerNumber(raw, float("".join(digits)), max(decimals, 0)))
        except ValueError:
            continue
    return out


def _matches(num: AnswerNumber, source: float) -> bool:
    target = abs(source)
    shown = num.value
    half_unit = 0.5 * 10 ** (-num.decimals)
    for exp in _SCALE_EXPONENTS:
        scale = 10**exp
        tol = half_unit * scale + _RELATIVE_TOLERANCE * target
        if abs(shown * scale - target) <= tol:
            return True
    return False


def _period(ev: EvidenceArtifact) -> tuple[date, date]:
    return ev.period_start.date(), ev.period_end.date()


def _date_parts(periods: set[tuple[date, date]]) -> set[float]:
    parts: set[float] = set()
    for start, end in periods:
        for d in (start, end):
            parts.update({float(d.year), float(d.month), float(d.day)})
    return parts


_HELD_PER_METRIC = 3


def _held(metric_ids: list[str], cited: set[str], evidence: dict[str, EvidenceArtifact]) -> str:
    """What the cited evidence holds for each metric, so a revision can print it
    instead of guessing which number the check wanted."""
    out = []
    for mid in metric_ids:
        rows = sorted(
            (evidence[e] for e in cited if evidence[e].metric_id == mid and evidence[e].grain == "none"
             and evidence[e].value is not None),
            key=lambda ev: (ev.period_start, sorted(ev.dimensions.items())),
        )[:_HELD_PER_METRIC]
        if rows:
            out.append(f"{mid}: " + ", ".join(
                f"{ev.value:,.2f} for {_period(ev)[0]}..{_period(ev)[1]}"
                + (f" {', '.join(f'{k}={v}' for k, v in ev.dimensions.items())}" if ev.dimensions else "")
                for ev in rows
            ))
    return "; ".join(out) or "see the evidence"


def _held_in(periods: list[tuple[date, date]], cited: set[str], evidence: dict[str, EvidenceArtifact]) -> str:
    """What the cited evidence holds for each period, metric by metric."""
    metrics = sorted({evidence[e].metric_id for e in cited if _period(evidence[e]) in periods})
    return _held(metrics, {e for e in cited if _period(evidence[e]) in periods}, evidence)


def check_answer_grounding(artifacts: list[Artifact], result: MissionResult | None) -> CheckOutcome:
    if result is None or not (result.final_response or "").strip():
        return CheckOutcome(check="answer_grounding", status="NOT_APPLICABLE")

    evidence: dict[str, EvidenceArtifact] = {}
    findings: dict[str, Finding] = {}
    for a in artifacts:
        try:
            if a.artifact_type == "evidence":
                evidence[a.id] = EvidenceArtifact.model_validate(a.payload)
            elif a.artifact_type == "finding":
                findings[a.id] = Finding.model_validate(a.payload)
        except Exception:
            continue

    cited_findings = [fid for fid in result.finding_ids if fid in findings]
    cited = {eid for eid in result.evidence_ids if eid in evidence}
    for fid in cited_findings:
        cited.update(eid for eid in findings[fid].evidence_ids if eid in evidence)
    if not cited and not cited_findings:
        cited = set(evidence)
        cited_findings = list(findings)
    if not cited:
        return CheckOutcome(check="answer_grounding", status="NOT_APPLICABLE")

    numbers = scan_numbers(result.final_response)

    # Per period: the evidence values, plus metrics of findings computed over
    # that period alone (a cross-period finding cannot vouch for either period).
    # Only period-total evidence (grain "none") is a per-period claim; a time
    # series is legitimately summarised as a trend, not reported point by point.
    all_sources: list[float] = []
    period_values: dict[tuple[date, date], list[float]] = {}
    # Per metric, for the same reason the period bucket exists. ``all_sources``
    # is metric-agnostic, so a number is "grounded" as long as SOME metric
    # happens to hold that value — which is how a column of one metric's values
    # reprinted under another metric's heading passes: a ROAS of 2.33 grounds a
    # conversion rate shown as 2.3308%. Requiring each cited metric to show at
    # least one of its OWN values catches that, because the metric whose column
    # was overwritten contributes nothing to the answer.
    metric_values: dict[str, list[float]] = {}
    for eid in cited:
        ev = evidence[eid]
        if ev.value is None:
            continue
        all_sources.append(float(ev.value))
        if ev.grain == "none":
            period_values.setdefault(_period(ev), []).append(float(ev.value))
            metric_values.setdefault(ev.metric_id, []).append(float(ev.value))
    for fid in cited_findings:
        f = findings[fid]
        vals = list(f.metrics.values())
        all_sources += vals
        periods = {_period(evidence[e]) for e in f.evidence_ids if e in evidence}
        if len(periods) == 1 and next(iter(periods)) in period_values:
            period_values[next(iter(periods))].extend(vals)

    # Evidence the answer shows through a finding computed from it: a per-day figure,
    # a span total summed from parts, a change. A finding names the metric each of its
    # figures belongs to in the figure's key ("ad_spend | ref"), so the figure stands
    # for that metric's evidence. Live 2026-10-08 (MS3-f23af5a954): the executor's
    # per-day ad spend and orders were reported exactly, yet "cites evidence for
    # ad_spend, orders but shows no value from it" spent three revisions.
    represented: set[str] = set()
    for fid in cited_findings:
        f = findings[fid]
        by_metric: dict[str, list[str]] = {}
        for eid in f.evidence_ids:
            if eid in evidence:
                by_metric.setdefault(evidence[eid].metric_id, []).append(eid)
        for key, value in f.metrics.items():
            named = {part.strip() for part in str(key).split("|")}
            named |= {part.split(".", 1)[0] for part in named}
            hits = [mid for mid in by_metric if mid in named]
            if hits and any(_matches(num, value) for num in numbers):
                for mid in hits:
                    represented.update(by_metric[mid])
    shown_periods = {_period(evidence[eid]) for eid in represented}
    shown_metrics = {evidence[eid].metric_id for eid in represented}

    uncovered = [
        p for p, vals in sorted(period_values.items())
        if p not in shown_periods and not any(_matches(num, v) for num in numbers for v in vals)
    ]
    silent_metrics = [
        mid for mid, vals in sorted(metric_values.items())
        if mid not in shown_metrics and not any(_matches(num, v) for num in numbers for v in vals)
    ]
    date_parts = _date_parts({_period(evidence[e]) for e in cited})
    ungrounded = [
        num.text for num in numbers
        if not any(_matches(num, v) for v in all_sources)
        and not (num.decimals == 0 and num.value in date_parts)
    ]

    out = CheckOutcome(check="answer_grounding")
    if uncovered:
        spans = ", ".join(f"{s}..{e}" if s != e else f"{s}" for s, e in uncovered)
        detail = (
            f"the answer cites evidence for {spans} but shows no value from it — report "
            f"the values the evidence holds for that period ({_held_in(uncovered, cited, evidence)}), "
            f"or drop that period's evidence if it is not part of the answer"
        )
        if ungrounded:
            detail += f"; numbers in the answer not found in any evidence: {', '.join(ungrounded)}"
        out.status = "INSUFFICIENT"
        out.gaps.append(EvidenceGap(description=detail, blocking=True, priority=8))
    elif silent_metrics:
        out.status = "INSUFFICIENT"
        out.gaps.append(
            EvidenceGap(
                description=(
                    f"the answer cites evidence for {', '.join(silent_metrics)} but shows no "
                    f"value from it — report that metric's own values ({_held(silent_metrics, cited, evidence)}), "
                    f"or drop its evidence and say the figure is unavailable rather than showing "
                    f"another metric's number in its place"
                ),
                blocking=True,
                priority=8,
            )
        )
    elif ungrounded:
        out.gaps.append(
            EvidenceGap(
                description=f"numbers in the answer not found in any evidence: {', '.join(ungrounded)}",
                priority=3,
            )
        )
    return out

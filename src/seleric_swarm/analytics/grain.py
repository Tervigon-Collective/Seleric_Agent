"""Grain preconditions for the Analytics toolset — ``CONTRACTS.md`` amendment A1.2.

Why this module exists
----------------------
``docs/BUG_SHEET.md`` #14: a multi-day evidence *sum* was compared against a
single-day baseline band, producing a false "+208% spike" for a metric that
was actually declining day over day. The shipped fix threads a typed
``granularity`` field from the LLM classifier through mission state so the
fetcher and the detector cannot disagree::

    llm_classifier.py:57  granularity: Literal["day","week","month","none"]
    intake/__init__.py:411
    graph.py:1194         mission.context["granularity"]
    observer.py:42        _daily_windows(tr) if context["granularity"] == "day"
    anomaly.py:44         per-evidence loop -> like-for-like comparison

That thread does not survive the V3 refactor. Non-negotiable rules 4 and 5
mean an analytics tool may neither fetch evidence nor call the tool that
does, so the *caller* — the LLM — picks the grain on every call and nothing
structurally ties the observation's grain to the baseline's. #14's fix would
port forward as a calling convention rather than a guarantee, and would
reappear as an intermittent rather than a test failure.

So the guarantee moves here: the tool validates what it was handed and
refuses a set it cannot compare like-for-like. Never normalizes, never
compares across grains silently. ``swarm/specialists/anomaly.py``'s
sum/normalize fallback branch is deliberately NOT ported — that was the
band-aid the real fix replaced.

Period convention
-----------------
``period_start``/``period_end`` are **inclusive**, matching the rest of this
repo: ``swarm/specialists/observer.py::_daily_windows`` emits one window per
day with ``start == end``, ``swarm/specialists/anomaly.py`` computes
``(end - start).days + 1``, and ``toolsets/semantic.py::query_metrics``
hands both dates to Cube as an inclusive range. A single day is therefore
span 1, not span 0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from seleric_swarm.agent.contracts import EvidenceArtifact

# Stamped onto every Finding this profile writes. Artifact.require_provenance()
# rejects a "derived" artifact with no calculation/query/prompt/tool/model
# version (conversations/contracts.py), and bumping this is how a change in
# the arithmetic becomes visible in stored provenance rather than silent.
CALCULATION_VERSION = "analytics.v1"

# Inclusive day-span each grain is allowed to cover. "month" is a range
# because calendar months genuinely differ (28-31); "day" and "week" are
# exact. "none" is unconstrained by design — it means "whatever window was
# asked for", so there is no declared grain to contradict.
_ALLOWED_SPAN_DAYS: dict[str, tuple[int, int]] = {
    "day": (1, 1),
    "week": (7, 7),
    "month": (28, 31),
}


def period_span_days(period_start: datetime, period_end: datetime) -> int:
    """Inclusive day count covered by a period. One calendar day -> 1.

    Compares dates, not instants: evidence periods are day-aligned in this
    system, and a tz-aware/naive mix in the raw datetimes must not make this
    raise (Profile A currently ships two divergent ``EvidenceArtifact``
    definitions whose ``fetched_at`` defaults differ in awareness — see
    ``TASK_SHEET.md``). ``.date()`` sidesteps that entirely.
    """
    return (period_end.date() - period_start.date()).days + 1


def validate_grain_set(evidence: list[EvidenceArtifact]) -> str | None:
    """Check an evidence set is internally comparable. ``None`` means OK.

    Returns a human-readable reason on violation rather than raising — the
    caller turns it into ``ToolResult(success=False,
    error_code="EVIDENCE_GRAIN_MISMATCH")``, and a tool must never raise
    across the agent boundary.

    Three rules, per ``CONTRACTS.md`` A1.2:

    1. every artifact in one call shares a single ``grain``;
    2. each artifact's period span matches the grain it declares;
    3. every span equals every other, so the observation is comparable to
       the baseline it is scored against.

    Rule 2 is the one that catches #14's actual shape: an artifact labelled
    ``grain="day"`` whose period covers five days is a window aggregate
    wearing a daily label, and dividing it by five (the old band-aid) is not
    the same number as a real daily series.
    """
    if not evidence:
        # Not a grain problem — an empty set is the caller's INSUFFICIENT_EVIDENCE
        # case, and reporting it as a mismatch would be misleading.
        return None

    grains = {e.grain for e in evidence}
    if len(grains) > 1:
        return f"evidence set mixes grains {sorted(grains)}; one call must use a single grain"

    grain = grains.pop()
    spans = [period_span_days(e.period_start, e.period_end) for e in evidence]

    allowed = _ALLOWED_SPAN_DAYS.get(grain)
    if allowed is not None:
        low, high = allowed
        for artifact, span in zip(evidence, spans, strict=True):
            if not low <= span <= high:
                expected = f"{low}" if low == high else f"{low}-{high}"
                return (
                    f"{artifact.metric_id} declares grain={grain!r} but its period "
                    f"{artifact.period_start.date()}..{artifact.period_end.date()} spans "
                    f"{span} day(s), expected {expected}; this is a window aggregate "
                    f"labelled as {grain}-grain, not a {grain} series"
                )

    if len(set(spans)) > 1:
        return (
            f"evidence periods span {sorted(set(spans))} days; an observation can only be "
            f"compared against a baseline covering the same number of days"
        )

    return None

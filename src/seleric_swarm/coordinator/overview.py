"""Sprint 5 — overview-shaped queries answered from stored
DomainStateSnapshots instead of a full live mission fan-out.

See docs/features/business-state-service/04_DOMAIN_HEALTH_SNAPSHOTS.md
("Overview answer path") and 05_SPRINT_PLAN.md Sprint 5. The classifier
already produces an ``executive_health`` intent for "how are we doing
today?"-style queries (coordinator/decomposition/templates.py's
``executive_health`` template, branches: commerce/performance/funnel/
finance/operations) -- today that intent still runs the full live
DECIDE->EXECUTE graph. This module is the fast path: read the latest
snapshot per branch domain and narrate directly, skipping decomposition/
planning/specialist fan-out entirely.

[Sprint 5 simplification] The 04 doc describes an LLM synthesizing the
snapshot(s); ``narrate_overview`` below builds the answer directly from
``DomainStateSnapshot`` fields instead -- both are equally Claim-Gate-safe
(the numbers are real either way), this just skips an LLM round trip a
template can already answer. Swap in an LLM narration pass later if
stakeholders want more natural phrasing.

[Sprint 5 simplification] The 04 doc also describes a live drill-down
dispatched *in parallel* with the snapshot read for the part of a question
a snapshot doesn't cover. That parallel-merge path isn't built -- instead,
``is_overview_query`` only takes the fast path for a *pure* "how are we
doing" ask (classifier produced only ``executive_health``, nothing else);
any query with a specific metric/domain/diagnostic ask falls straight
through to the existing full live pipeline in graph.py, unchanged. This
satisfies "a drill-down follow-up still gets a live, accurate answer"
without a new merge system -- it just never qualifies for the shortcut.

[Bug found + fixed post-Sprint-5, 2026-09-15 #1] ``is_overview_query`` used
to also take a ``forced`` bool (True if any of full_diagnostic/
full_prediction/full_skeptic/full_strategy was set) and skip the shortcut
whenever it was True, on the theory that those flags mean "the caller
explicitly wants the deep pipeline." That's wrong for this API: ``main.py``
's ``MissionRequest`` defaults all four to ``True`` for *every* request
(including the Swagger example body), so ``forced`` was true for virtually
all real traffic -- the overview shortcut could never fire in production
regardless of the query. The classifier's ``normalized.intents`` is already
the correct, query-specific signal (a pure "how are we doing" ask
classifies to exactly ``{"executive_health"}``, nothing else); the full_*
flags are an API-wide default posture, not a per-query escalation signal,
and are no longer consulted here.

[Bug found + fixed post-Sprint-5, 2026-09-15 #2] The classifier tags
domain-specific health questions ("how is attribution doing") with the
*same* ``intents=["executive_health"]`` as a fully generic "how are we
doing today" -- and its ``candidate_domains`` for that query didn't even
include "attribution". Live trace: "how is attribution doing" hit the
overview shortcut and answered with the fixed ``OVERVIEW_DOMAINS`` 5-domain
dump (commerce/performance/funnel/finance/operations) -- attribution was
never mentioned, because it isn't even in that list. Fix:
``overview_domains_for_query`` deterministically checks whether the query
names one of `services.domain_health.scheduler.ALL_DOMAINS` (all 8, not
just the 5-branch template) and scopes the snapshot read to just that
domain when it does; falls back to the generic ``OVERVIEW_DOMAINS`` only
when no domain is named. No LLM guessing -- same Claim-Gate-safe,
deterministic-string-match style as ``narrate_overview``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from seleric_swarm.services.domain_health.models import DomainStateSnapshot
from seleric_swarm.services.domain_health.scheduler import ALL_DOMAINS
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore
from seleric_swarm.swarm.mission import SwarmMissionResult

if TYPE_CHECKING:
    from seleric_swarm.coordinator.contracts import NormalizedQuery

# Matches decomposition/templates.py's "executive_health" template branches.
# Used only when the query names no specific domain (a truly generic ask) --
# see overview_domains_for_query.
OVERVIEW_DOMAINS = ["commerce", "performance", "funnel", "finance", "operations"]
# Daily cron cadence (services/domain_health/scheduler.py) + a buffer.
MAX_SNAPSHOT_AGE_HOURS = 36.0
_ARTIFACT_TYPES = ("evidence", "anomaly", "hypothesis", "causal", "prediction", "strategy", "skeptic")


def is_overview_query(normalized: NormalizedQuery) -> bool:
    """True only for a pure "how are we doing" ask -- the classifier
    produced exactly ``{"executive_health"}``, nothing else. Deliberately
    does NOT look at the full_diagnostic/full_prediction/full_skeptic/
    full_strategy request flags (see module docstring's 2026-09-15 fix
    note): they default to True for every request at the API layer, so
    they can't distinguish an explicit escalation from an unmodified
    default body.
    """
    return set(normalized.intents) == {"executive_health"}


def overview_domains_for_query(query: str, all_domains: list[str] = ALL_DOMAINS) -> list[str]:
    """Scope the snapshot read to a domain the query actually names (e.g.
    "how is attribution doing" -> ["attribution"]), falling back to the
    generic OVERVIEW_DOMAINS set for a truly generic ask ("how are we
    doing today"). Word-boundary substring match against the
    domain_health config's own domain names -- deterministic, no LLM.
    """
    q = query.lower()
    named = [d for d in all_domains if re.search(rf"\b{re.escape(d)}\b", q)]
    return named or OVERVIEW_DOMAINS


def _is_stale(snapshot: DomainStateSnapshot, *, now: datetime, max_age_hours: float) -> bool:
    try:
        computed_at = datetime.fromisoformat(snapshot.computed_at)
    except ValueError:
        return True
    age_hours = (now - computed_at).total_seconds() / 3600
    return age_hours > max_age_hours


_STATUS_PLAIN = {
    "OK": "healthy",
    "DEGRADED": "some data gaps",
    "UNAVAILABLE": "data unavailable",
}


def _plain_unavailable_reason(reason: str) -> str:
    if reason.startswith("snapshot stale"):
        return "data is out of date"
    if reason == "no snapshot available":
        return "data isn't ready yet"
    return "data isn't ready yet"


async def read_overview_snapshots(
    store: SnapshotStore,
    domains: list[str] = OVERVIEW_DOMAINS,
    *,
    max_age_hours: float = MAX_SNAPSHOT_AGE_HOURS,
) -> tuple[list[DomainStateSnapshot], list[tuple[str, str]]]:
    """Returns ``(fresh_snapshots, unavailable)``. ``unavailable`` is
    ``(domain, reason)`` for every domain that's missing or stale -- never
    silently dropped, always surfaced by the caller as a limitation.
    """
    now = datetime.now(UTC)
    fresh: list[DomainStateSnapshot] = []
    unavailable: list[tuple[str, str]] = []
    for domain in domains:
        snapshot = await store.aget_latest(domain)
        if snapshot is None:
            unavailable.append((domain, "no snapshot available"))
        elif _is_stale(snapshot, now=now, max_age_hours=max_age_hours):
            unavailable.append((domain, f"snapshot stale (computed_at={snapshot.computed_at})"))
        else:
            fresh.append(snapshot)
    return fresh, unavailable


_DETAIL_WORDS = re.compile(
    r"\b(detail|details|detailed|elaborate|breakdown|in depth|in-depth|explain)\b", re.IGNORECASE
)


def _wants_detail(query: str) -> bool:
    """A query asking to elaborate deserves more than the one-line headline
    narration -- the per-metric values are already on the snapshot
    (``ResolvedMetric``), narrate_overview just wasn't reading them."""
    return bool(_DETAIL_WORDS.search(query))


def _metric_detail_line(m: Any) -> str:
    value = "no data" if m.value is None else f"{m.value:,.2f}"
    delta = "" if m.period_delta_pct is None else f" (Δ {m.period_delta_pct:+.1f}%)"
    return f"  - {m.metric_id}: {value}{delta}"


def narrate_overview(
    snapshots: list[DomainStateSnapshot], unavailable: list[tuple[str, str]], *, detail: bool = False
) -> tuple[str, list[str]]:
    """Deterministic narration straight from snapshot fields. Returns
    ``(final_response, limitations)``. ``detail=True`` appends every
    resolved metric's value/delta under each domain's headline line instead
    of only the metrics that tripped a threshold.
    """
    lines = []
    for s in snapshots:
        if s.headline_signals:
            lines.append(f"{s.domain}: " + "; ".join(s.headline_signals))
        else:
            status_plain = _STATUS_PLAIN.get(s.status, "status unknown")
            lines.append(f"{s.domain}: no threshold breaches ({status_plain}).")
        if detail:
            lines.extend(_metric_detail_line(m) for m in s.metrics)
    if not lines:
        lines.append(
            "I couldn’t load a current business overview yet. The live data sources "
            "are unavailable or the scheduled health snapshots have not completed."
        )
    limitations = [
        f"{domain}: {_plain_unavailable_reason(reason)}." for domain, reason in unavailable
    ]
    return "\n".join(lines), limitations


def build_overview_result(
    *,
    mission_id: str,
    query: str,
    snapshots: list[DomainStateSnapshot],
    unavailable: list[tuple[str, str]],
) -> SwarmMissionResult:
    final_response, limitations = narrate_overview(snapshots, unavailable, detail=_wants_detail(query))
    status = "completed" if snapshots and not unavailable else "partial"
    return SwarmMissionResult(
        mission_id=mission_id,
        status=status,
        query=query,
        complexity="L0",
        initial_mission_lead="coordinator_agent",
        mission_lead="coordinator_agent",
        leadership_epoch=0,
        team=[],
        handoff_history=[],
        artifacts={t: [] for t in _ARTIFACT_TYPES},
        final_response=final_response,
        limitations=limitations,
        synthetic=False,
        events=[],
    )

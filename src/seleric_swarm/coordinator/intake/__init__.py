"""Query intake — LLM+catalogue classification only.

There is intentionally no local intent keyword table, no ``_METRIC_ALIASES``
alias dictionary, and no regex-based domain guesser here. Every semantic
decision (intent, primary metric, entities, domain lead) comes from the LLM
classifier grounded in the live metric registry + Seleric catalogue via
``coordinator.intake.llm_classifier``.

When no ``runtime`` is available, or the LLM call itself fails, this module
fails closed: the returned ``NormalizedQuery`` carries
``unsupported_reason=LLM_CLASSIFICATION_UNAVAILABLE`` and empty intents. The
mission entrypoint (``coordinator.graph.run_swarm_v2_mission``) treats that
as an ``UNSUPPORTED`` mission — it does not fabricate a heuristic classification.

Regex is still the right tool for date-token parsing (ISO dates, "last N days",
"yesterday"). That logic lives in :mod:`seleric_swarm.services.time_range`
(``window_from_query`` / ``resolve_time_range``) and is invoked by the LLM
classifier, which is a syntactic tokenizer — not intent classification.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from seleric_swarm.coordinator.contracts import (
    DomainQuestion,
    EntityRef,
    NormalizedQuery,
    TimeRange,
)
from seleric_swarm.services.metrics import MetricRegistry, lead_agent_for_hints

UNSUPPORTED_NO_LLM = "LLM_CLASSIFICATION_UNAVAILABLE"

# When the classifier cannot pin the mission to a specific domain (e.g.
# ``executive_health``), fan out across the domains a broad investigation
# needs to touch. Ordered by the workspace's typical CAC→conversion→profit
# investigation chain — the mission planner picks the first as initial lead.
_BROAD_INVESTIGATION_DOMAINS = ["commerce", "performance", "funnel", "finance"]


def apply_full_flags(
    intents: set[str] | list[str],
    *,
    full_diagnostic: bool = False,
    full_prediction: bool = False,
    full_skeptic: bool = False,
    full_strategy: bool = False,
) -> set[str]:
    """Fold ``full_*`` request flags into the intent set.

    The HTTP API sets these to ``True`` by default so callers expect the
    matching specialist to *run*, not just be registered. Without this,
    ``full_prediction=True`` on a pure "why" query never activates
    ``PredictionAgent``.
    """

    out = set(intents)
    if full_diagnostic:
        out.add("diagnostic")
    if full_prediction:
        out.add("predictive")
    if full_strategy:
        out.add("prescriptive")
    if full_skeptic and not (out & {"diagnostic", "predictive", "prescriptive", "executive_health"}):
        # Skeptic needs a claim-bearing path; diagnostic is the minimum.
        out.add("diagnostic")
    return out


def candidate_domains(
    intents: list[str] | set[str],
    primary_metric: str | None,
    metrics: MetricRegistry | None = None,
    extra_metrics: Sequence[str] | None = None,
) -> list[str]:
    """Derive the mission's candidate domains from registry metric ownership.

    Never from keyword matching over the query text. The metric registry is
    the single source of truth for which domain owns a metric; this function
    is a pure lookup.

    * ``executive_health`` fans out to the broad investigation set.
    * Every hinted metric (primary first, then extras) contributes its owner.
      Cross-domain asks such as units sold + net profit become
      ``[product, finance]``, not a single primary-only lead.
    * Anything else returns ``[]`` — the caller is expected to fall through
      to a documented default lead (``commerce_agent`` today) rather than
      guessing another domain here.
    """

    intent_set = set(intents)
    if "executive_health" in intent_set:
        return list(_BROAD_INVESTIGATION_DOMAINS)
    if metrics is None:
        return []
    ordered: list[str] = []
    for mid in [primary_metric, *(extra_metrics or [])]:
        if not mid:
            continue
        lead = lead_agent_for_hints([mid], metrics)
        if lead == "coordinator_agent":
            continue
        domain = lead.removesuffix("_agent")
        if domain not in ordered:
            ordered.append(domain)
    return ordered


def _supported_dimensions_for(metric_id: str, metrics: MetricRegistry) -> set[str]:
    """Catalogue dims this id can slice. Empty means unknown — grain is dropped."""
    out: set[str] = set()
    seen: set[str] = set()
    pending = [metric_id]
    while pending:
        mid = pending.pop()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        defn = metrics.get(mid)
        if defn is None:
            continue
        raw = getattr(defn, "raw", None) or {}
        out.update(str(d) for d in (raw.get("supported_dimensions") or []) if d)
        cat = getattr(defn, "catalogue_metric", None)
        if cat and cat not in seen:
            pending.append(str(cat))
    return out


def partition_domain_questions(
    *,
    original_query: str,
    metric_ids: Sequence[str],
    grain: Sequence[str] = (),
    metrics: MetricRegistry | None = None,
) -> list[DomainQuestion]:
    """Split a classified metric bag into one retrieve question per owning domain.

    Grain is the already-resolved dimension list from catalogue grounding —
    it is attached as-is, not guessed from the query text. Metrics the
    registry cannot place stay out of the partition rather than inventing
    a domain.
    """

    if metrics is None:
        return []
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for mid in metric_ids:
        if not mid:
            continue
        lead = lead_agent_for_hints([mid], metrics)
        if lead == "coordinator_agent":
            continue
        domain = lead.removesuffix("_agent")
        if domain not in grouped:
            grouped[domain] = []
            order.append(domain)
        if mid not in grouped[domain]:
            grouped[domain].append(mid)
    grain_list = [g for g in grain if g]
    out: list[DomainQuestion] = []
    for domain in order:
        mids = grouped[domain]
        supported: set[str] = set()
        declared = False
        for mid in mids:
            dims = _supported_dimensions_for(mid, metrics)
            if dims:
                declared = True
            supported.update(dims)
        if declared:
            # At least one metric here declares an explicit allowlist —
            # honor it (a metric that lists dims A/B but not C genuinely
            # can't slice by C).
            domain_grain = [g for g in grain_list if g in supported]
        else:
            # No metric in this domain/registry declares supported_dimensions
            # (true for every entry in metric_registry.yaml today, since the
            # field has never been populated) -- `grain` already passed live
            # catalogue validation upstream (apply_catalogue_grain resolves
            # entities against the real catalogue before this function ever
            # runs), so trust it instead of silently discarding every
            # breakdown request because of an unfilled registry field.
            domain_grain = grain_list
        metric_bit = ", ".join(mids)
        if domain_grain:
            question = (
                f"[{domain}] {original_query} — retrieve {metric_bit} "
                f"sliced by {', '.join(domain_grain)}."
            )
        else:
            question = f"[{domain}] {original_query} — retrieve {metric_bit}."
        out.append(
            DomainQuestion(domain=domain, metrics=mids, grain=domain_grain, question=question)
        )
    return out


def metric_hints_for_mission(normalized: NormalizedQuery) -> list[str]:
    """Primary + secondary, de-duplicated, primary first. Never secondary-only."""

    return list(
        dict.fromkeys(
            m for m in [normalized.primary_metric, *normalized.secondary_metrics] if m
        )
    )


def _metric_needed(intents: Sequence[str]) -> bool:
    """True when the intent set requires a resolved primary metric to
    proceed at all — executive_health legitimately scans every domain with
    no single metric, so it's exempt.
    """
    intents = list(intents)
    return bool(intents) and "executive_health" not in intents and (
        "lookup" in intents
        or any(i in intents for i in ("diagnostic", "predictive", "prescriptive", "comparison"))
    )


def _grain_from_entities(entities: Sequence[EntityRef]) -> list[str]:
    grain: list[str] = []
    for entity in entities:
        gid = entity.entity_id or entity.raw
        if gid and gid not in grain:
            grain.append(gid)
    return grain


UNSUPPORTED_PRIMARY_METRIC_UNRESOLVED = "PRIMARY_METRIC_UNRESOLVED"

# Machine-readable tokens that live inside NormalizedQuery.unresolved_semantics.
# Callers (coordinator.graph) match against these constants, not literal strings,
# so a rename here can't drift out of sync silently.
UNRESOLVED_NO_LLM = UNSUPPORTED_NO_LLM.lower()
UNRESOLVED_PRIMARY_METRIC = "primary_metric_unresolved"


def _unsupported(
    query: str,
    requested_outputs: list[str] | None,
    *,
    reason: str,
    unresolved_semantics: list[str] | None = None,
) -> NormalizedQuery:
    """Fail-closed NormalizedQuery — empty intents, explicit unsupported_reason."""

    return NormalizedQuery(
        original_query=query,
        intents=[],
        primary_metric=None,
        secondary_metrics=[],
        entities=[],
        time_range=None,
        comparison_range=None,
        requested_outputs=list(requested_outputs or []),
        candidate_domains=[],
        domain_questions=[],
        unresolved_semantics=list(unresolved_semantics or [reason.lower()]),
        metric_resolution_reason=None,
        unsupported_reason=reason,
    )


async def normalize_query(
    query: str,
    *,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    metrics: MetricRegistry | None = None,
    requested_outputs: list[str] | None = None,
    agent_id: str = "coordinator_agent",
    runtime: Any | None = None,
    mission_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    context_bundle: dict[str, Any] | None = None,
) -> NormalizedQuery:
    """Classify a natural-language query via the LLM + live catalogue.

    Fails closed with:

    * ``LLM_CLASSIFICATION_UNAVAILABLE`` when no runtime is given or the LLM
      call fails — never falls back to keyword regexes.
    * ``PRIMARY_METRIC_UNRESOLVED`` when the query has a diagnostic/predictive/
      prescriptive/lookup intent but no metric could be resolved against the
      registry — fail at the intake boundary, not deep in the pipeline.
    """

    if runtime is None:
        return _unsupported(query, requested_outputs, reason=UNSUPPORTED_NO_LLM)

    # Lazy import breaks a cycle: intake -> llm_classifier -> runtime -> intake.
    from seleric_swarm.coordinator.intake.llm_classifier import classify_query_via_llm

    llm_result = await classify_query_via_llm(
        query,
        runtime=runtime,
        timezone=timezone,
        as_of=as_of,
        agent_id=agent_id,
        mission_id=mission_id,
        request_id=request_id,
        session_id=session_id,
        context_bundle=context_bundle,
    )
    if llm_result is None:
        return _unsupported(query, requested_outputs, reason=UNSUPPORTED_NO_LLM)

    if llm_result.primary_metric is None and _metric_needed(llm_result.intents):
        # Classification is occasionally non-deterministic even at
        # temperature=0 — the same query can resolve a primary metric on one
        # call and not on the next (confirmed directly: repeated calls on an
        # identical query flip between PRIMARY_METRIC_UNRESOLVED and a clean
        # result). This is the swarm-internal classify call, downstream of
        # dispatch.py's own routing retry — a flaky miss here surfaces as
        # PRIMARY_METRIC_UNRESOLVED even after routing correctly picked
        # "swarm". One retry before failing the whole mission.
        retry_result = await classify_query_via_llm(
            query,
            runtime=runtime,
            timezone=timezone,
            as_of=as_of,
            agent_id=agent_id,
            mission_id=mission_id,
            request_id=request_id,
            session_id=session_id,
            context_bundle=context_bundle,
        )
        if retry_result is not None:
            llm_result = retry_result

    intents = list(llm_result.intents)
    primary = llm_result.primary_metric
    secondary = list(llm_result.secondary_metrics)
    reason = f"llm+catalogue: {primary}" if primary else None

    entities = [
        EntityRef(
            entity_type="dimension",
            entity_id=e,
            raw=e,
            resolved=True,
            resolution_reason="llm_catalogue",
        )
        for e in llm_result.entities
    ]

    tr = llm_result.time_range
    time_range = (
        TimeRange(
            start=tr.start,
            end=tr.end or tr.start,
            timezone=timezone,
            label=tr.relative_token,
        )
        if tr.start
        else None
    )
    comparison_range = (
        TimeRange(start=tr.start_b, end=tr.end_b or tr.start_b, timezone=timezone, label=tr.relative_token)
        if tr.kind == "comparison" and tr.start_b
        else None
    )

    from_metrics = candidate_domains(intents, primary, metrics, extra_metrics=secondary)
    if "executive_health" in intents:
        domains = from_metrics
    else:
        llm_lead = (
            [llm_result.domain_lead.removesuffix("_agent")]
            if llm_result.domain_lead
            else []
        )
        domains = list(dict.fromkeys([*llm_lead, *from_metrics]))
    grain = _grain_from_entities(entities)
    domain_qs = (
        []
        if "executive_health" in intents
        else partition_domain_questions(
            original_query=query,
            metric_ids=[m for m in [primary, *secondary] if m],
            grain=grain,
            metrics=metrics,
        )
    )

    # Intake-boundary fail-closed: a targeted investigation (why/forecast/action)
    # against a query whose primary metric is unresolvable is unsupported — the
    # downstream pipeline can't investigate a metric it can't identify. Executive
    # health is exempt: it legitimately scans every domain, no single metric.
    if primary is None and _metric_needed(intents):
        return _unsupported(
            query,
            requested_outputs,
            reason=UNSUPPORTED_PRIMARY_METRIC_UNRESOLVED,
            unresolved_semantics=[UNRESOLVED_PRIMARY_METRIC],
        )

    unresolved: list[str] = []

    return NormalizedQuery(
        original_query=query,
        intents=intents,
        primary_metric=primary,
        secondary_metrics=secondary,
        entities=entities,
        time_range=time_range,
        comparison_range=comparison_range,
        granularity=llm_result.granularity,
        requested_outputs=list(requested_outputs or []),
        candidate_domains=domains,
        domain_questions=domain_qs,
        unresolved_semantics=unresolved,
        metric_resolution_reason=reason,
        unsupported_reason=llm_result.unsupported_reason if llm_result.unresolved else None,
    )


def resolve_mission_time_range(
    scenario: dict,
    *,
    timezone: str,
    as_of: str | None = None,
    normalized: NormalizedQuery | None = None,
) -> dict[str, str | None]:
    """Build the observation window used for MCP fetches.

    Preference order:

    1. Scenario ``observation_window`` (preserves fixture degradation arcs)
    2. Query-derived ``normalized.time_range`` when no scenario window
    3. ``as_of`` alone

    When ``as_of`` is past the window end, extend ``end`` to ``as_of`` so
    MCP fetches include the client observation day (without rewriting the
    start of the investigated window).
    """

    window = dict(scenario.get("observation_window") or {})
    as_of_day: str | None = None
    if as_of:
        raw = str(as_of)[:10]
        try:
            as_of_day = date.fromisoformat(raw).isoformat()
        except ValueError as exc:
            # A malformed as_of is a caller bug that would silently produce an
            # unbounded time window — surface it instead of swallowing.
            raise ValueError(f"Invalid as_of={as_of!r}; expected ISO date") from exc

    start = str(window["start"])[:10] if window.get("start") else None
    end = str(window["end"])[:10] if window.get("end") else None
    # Preserve the scenario/fixture arc end for single-day MCP fetches even
    # if client ``as_of`` extends the reported observation window.
    observation_end = end
    # Period B, comparison queries only ("June vs August") — carried through
    # so ObserverAgent can fetch both periods instead of collapsing to one.
    start_b: str | None = None
    end_b: str | None = None
    comparison = False

    if (start is None or end is None) and normalized is not None and normalized.time_range is not None:
        tr = normalized.time_range
        n_start = getattr(tr, "start", None)
        n_end = getattr(tr, "end", None) or n_start
        if n_start and n_end:
            start = start or str(n_start)[:10]
            end = end or str(n_end)[:10]
            observation_end = observation_end or end
        # Period B lives on comparison_range, not time_range — NormalizedQuery.
        # time_range is the plain TimeRange model (start/end/timezone/label
        # only); TimeRangeV1's start_b/end_b never survive onto it.
        cr = normalized.comparison_range
        if cr is not None and cr.start:
            start_b = str(cr.start)[:10]
            end_b = str(cr.end or cr.start)[:10]
            comparison = True

    # A comparison's periods are already two fully-resolved explicit ranges —
    # stretching period A's end out to `as_of` (meant for "include today in
    # an open-ended window") would silently turn "June vs July" into
    # "June-through-today vs July".
    if as_of_day and not comparison:
        end = max(end or as_of_day, as_of_day)
        if not start or start > end:
            start = as_of_day if not start else min(start, as_of_day)

    if not start and not end:
        start = end = as_of_day
        observation_end = as_of_day

    out: dict[str, str | None] = {"start": start, "end": end, "timezone": timezone}
    if observation_end:
        out["observation_end"] = observation_end
    if comparison:
        out["kind"] = "comparison"
        out["start_b"] = start_b
        out["end_b"] = end_b
    return out


def complexity_band(normalized: NormalizedQuery) -> str:
    """Derive the L0–L5 complexity band from the intent set.

    Pure set logic — no textual analysis of the query.
    """

    intents = set(normalized.intents)
    if intents <= {"lookup"}:
        return "L0"
    if intents <= {"lookup", "comparison"} or intents == {"comparison"}:
        return "L1" if "comparison" not in intents else "L2"
    if "executive_health" in intents and not (intents & {"diagnostic", "predictive", "prescriptive"}):
        return "L2"
    if "prescriptive" in intents:
        return "L5"
    if "predictive" in intents and "diagnostic" in intents:
        return "L5"
    if "predictive" in intents:
        return "L4"
    if "diagnostic" in intents:
        return "L4"
    return "L3"


def intent_band_for_activation(normalized: NormalizedQuery) -> str:
    """Map intents to a coarse activation band the CoordinatorPolicies use."""

    intents = set(normalized.intents)
    if "prescriptive" in intents:
        return "PRESCRIPTIVE"
    if "predictive" in intents and "diagnostic" not in intents:
        return "PREDICTIVE"
    if "diagnostic" in intents:
        return "DIAGNOSTIC"
    if "executive_health" in intents:
        return "ANOMALY"
    if "comparison" in intents:
        return "COMPARISON"
    if "lookup" in intents:
        return "LOOKUP"
    return "DIAGNOSTIC"

"""LLM-based query classification for swarm_v2.

There is no keyword/regex intent matcher. Regex stays only where it is the
right tool: date-token parsing (``services/time_range.window_from_query``
handles "last 3 days" / "yesterday" / ISO dates deterministically before
the LLM's own ``time_range`` guess is used as a fallback). That is a
syntactic tokenizer, not intent classification.

When the LLM path is unusable (no prompt / no LLM configured / call failed)
this function returns ``None`` and the caller (``coordinator.intake.normalize_query``)
surfaces ``LLM_CLASSIFICATION_UNAVAILABLE`` — nothing falls back to a
keyword table.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.coordinator.catalogue_grounding import (
    collapse_assigned_metrics,
    validate_dimensions_for_metric,
)
from seleric_swarm.coordinator.intake.conversation_context import (
    cache_fingerprint,
    context_fingerprint,
    format_prior_turns,
    inherit_metric_source,
    inherit_time_range,
    leftover_tokens,
)
from seleric_swarm.llm.errors import LLMError, LLMStructuredOutputError
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.services.metrics import lead_agent_for_hints
from seleric_swarm.services.time_range import resolve_time_range, window_from_query
from seleric_swarm.utils.ttl_cache import TTLCache

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime

AGENT_VERSION = "0.1.0"

SwarmIntent = Literal[
    "lookup", "comparison", "diagnostic", "predictive", "prescriptive", "executive_health"
]


class SwarmClassificationV1(BaseModel):
    intents: list[SwarmIntent] = Field(default_factory=list)
    domain_lead: str = ""
    entities: list[str] = Field(default_factory=list)
    time_range: TimeRangeV1 = Field(default_factory=TimeRangeV1)
    metric_hints: list[str] = Field(default_factory=list)
    # Real catalogue dimension ids the resolved metric supports (from the
    # "[dims: ...]" list in registry_catalog), not English nouns — the
    # direct-parameter output Phase 1 adds so the pipeline doesn't have to
    # re-derive grain from query text (docs/BUG_SHEET.md #8).
    dimensions: list[str] = Field(default_factory=list)
    # "day" when the question investigates change over a multi-day window
    # (why/diagnostic, or an explicit per-day ask) so evidence is fetched as
    # a real daily series instead of one window aggregate
    # (docs/BUG_SHEET.md #14). "none" is the default single-aggregate fetch.
    granularity: Literal["day", "week", "month", "none"] = "none"
    unsupported_reason: str | None = None


class LlmClassification(BaseModel):
    """What coordinator/intake.normalize_query needs from the classifier —
    provider-agnostic so normalize_query doesn't depend on LLM plumbing types.
    """

    intents: list[str]
    domain_lead: str
    entities: list[str]
    time_range: TimeRangeV1
    primary_metric: str | None
    secondary_metrics: list[str]
    granularity: str = "none"
    unresolved: bool
    unsupported_reason: str | None


# Repeated identical questions (a user re-asking, a retry, a dashboard poll)
# reuse the last classification instead of paying for another LLM call.
# Keyed on query + timezone + as_of + inherited window + recent thread turns
# so a follow-up like "yesterday?" after gross sales does not reuse a CAC
# classification from another thread. Only successful classifications are
# cached, so an LLM outage never gets stuck as a permanent failure.
_CLASSIFICATION_CACHE: TTLCache[tuple[str, str, str, str, str], LlmClassification] = TTLCache(
    maxsize=256, ttl_s=300.0
)
_METRIC_INHERIT: ContextVar[bool] = ContextVar("seleric_metric_inherit", default=False)


def _classification_cache_key(
    query: str,
    timezone: str,
    as_of: str | None,
    inherited: TimeRangeV1 | None,
    context_bundle: Mapping[str, Any] | None,
) -> tuple[str, str, str, str, str]:
    normalized = " ".join(query.casefold().split())
    return (
        normalized,
        timezone,
        as_of or "",
        cache_fingerprint(inherited),
        context_fingerprint(context_bundle),
    )


async def classify_query_via_llm(
    query: str,
    *,
    runtime: SwarmRuntime,
    timezone: str,
    as_of: str | None,
    mission_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    agent_id: str = "coordinator_agent",
    context_bundle: Mapping[str, Any] | None = None,
) -> LlmClassification | None:
    """Classify intent/metrics/domain/entities via the LLM + live catalogue.

    Returns ``None`` when the LLM path isn't usable (no prompt, no LLM
    configured, or the call failed). Callers surface the failure as
    ``LLM_CLASSIFICATION_UNAVAILABLE``; there is no keyword fallback.
    """
    inherited = inherit_time_range(
        query, timezone=timezone, as_of=as_of, context_bundle=context_bundle
    )
    cache_key = _classification_cache_key(
        query, timezone, as_of, inherited, context_bundle
    )
    cached = _CLASSIFICATION_CACHE.get(cache_key)
    if cached is not None:
        return cached.model_copy(deep=True)

    try:
        spec = runtime.prompts.load("coordinator.classify_swarm")
    except Exception:
        return None

    user = spec.render_user(
        {
            "query": query,
            "timezone": timezone,
            "as_of": as_of or "none",
            "registry_catalog": runtime.metrics.catalog_prompt(),
        }
    )
    prior_turns = format_prior_turns(context_bundle)
    if prior_turns:
        user = (
            f"{user}\nPrior user turns (follow-up context only; the Query "
            f"line above is the current ask):\n{prior_turns}"
        )
    request = LLMRequest(
        messages=[
            ChatMessage(role="system", content=spec.system),
            ChatMessage(role="user", content=user),
        ],
        model=spec.model,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        timeout_s=runtime.settings.llm_timeout_s,
        metadata=LLMRequestMetadata(
            request_id=request_id or uuid4().hex,
            session_id=session_id or uuid4().hex,
            mission_id=mission_id or uuid4().hex,
            agent_id=agent_id,
            agent_version=AGENT_VERSION,
            prompt_id=spec.id,
            prompt_version=spec.version,
            workflow_name=runtime.settings.workflow_name,
            workflow_version=runtime.settings.workflow_version,
            model=spec.model,
        ),
        tags=["coordinator", "classify_swarm", spec.id],
    )
    try:
        result = await runtime.llm.complete_structured(request, SwarmClassificationV1)
    except (LLMStructuredOutputError, LLMError):
        return None

    classification: SwarmClassificationV1 = result.value

    # Regex is the right tool for date tokens — try it before trusting the
    # LLM's own time_range guess. A follow-up that omits the period inherits
    # the last explicit window from this thread instead of defaulting to
    # yesterday / last_7d.
    window = window_from_query(query, timezone, as_of) or inherited
    try:
        resolved_window = window or resolve_time_range(classification.time_range, timezone, as_of)
    except ValueError:
        resolved_window = TimeRangeV1()

    # Trust the LLM's own metric_hints/dimensions directly — they were
    # picked against the real catalogue listed in registry_catalog
    # (services/metrics.MetricRegistry.catalog_prompt), not re-derived from
    # query text via keyword/alias matching. An id the LLM invented simply
    # doesn't resolve here and drops out; normalize_query's existing
    # one-shot reclassification retry (coordinator/intake) handles the case
    # where that leaves no primary metric at all.
    bootstrap = getattr(runtime, "bootstrap", None)
    canonical = collapse_assigned_metrics(
        [m for m in classification.metric_hints if runtime.metrics.get(m) is not None],
        runtime.metrics,
        query,
        bootstrap,
    )
    if not canonical and not _METRIC_INHERIT.get() and not leftover_tokens(query):
        source = inherit_metric_source(
            query, context_bundle=context_bundle, timezone=timezone, as_of=as_of
        )
        if source:
            token = _METRIC_INHERIT.set(True)
            try:
                prior = await classify_query_via_llm(
                    source,
                    runtime=runtime,
                    timezone=timezone,
                    as_of=as_of,
                    mission_id=mission_id,
                    request_id=request_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    context_bundle=None,
                )
            finally:
                _METRIC_INHERIT.reset(token)
            if prior is not None and prior.primary_metric:
                canonical = [
                    mid
                    for mid in [prior.primary_metric, *prior.secondary_metrics]
                    if runtime.metrics.get(mid) is not None
                ]
    resolved_dimensions = validate_dimensions_for_metric(
        classification.dimensions, canonical[0] if canonical else None, runtime.metrics
    )

    entities = list(resolved_dimensions or [])

    # "coordinator_agent" is the orchestrating role, never a domain lead —
    # treat it the same as "no lead determined" so callers fall back safely.
    domain_lead = classification.domain_lead
    if domain_lead in {"coordinator_agent", "coordinator"}:
        domain_lead = ""
    if canonical:
        registry_lead = lead_agent_for_hints(canonical, runtime.metrics)
        if registry_lead != "coordinator_agent":
            domain_lead = registry_lead

    resolved = LlmClassification(
        intents=list(classification.intents) or ["lookup"],
        domain_lead=domain_lead,
        entities=entities,
        time_range=resolved_window,
        primary_metric=canonical[0] if canonical else None,
        secondary_metrics=canonical[1:],
        granularity=classification.granularity,
        unresolved=not canonical and classification.unsupported_reason is not None,
        unsupported_reason=classification.unsupported_reason,
    )
    _CLASSIFICATION_CACHE.set(cache_key, resolved.model_copy(deep=True))
    return resolved

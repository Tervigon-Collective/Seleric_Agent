"""Run one V3 mission and persist it where the UI already reads.

Conversations (``api/conversations.py``) pick ``final_response`` off the
swarm_v2 mission store after ``run_mission_job``. The Office UI reads
``GET /v1/office/missions*``. Both stay unchanged: this runner writes the
V3 result into those existing shapes (via ``v3_adapter``) and into the V3
stores the office gateway falls back to.
"""

from __future__ import annotations

import dataclasses
import json

import asyncio
import logging
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openai import APITimeoutError as OpenAIAPITimeoutError
from pydantic_ai.exceptions import ModelHTTPError

from seleric_swarm.agent.agent import CONVERSATIONAL, build_seleric_agent, capability_manifest
from seleric_swarm.agent.dependencies import (
    ExecutionLimits,
    JevConfig,
    NullMcpClient,
    SelericDeps,
)
from seleric_swarm.agent.intent import QueryClassification, classify_query, stated_grain
from seleric_swarm.api.status import is_terminal_status
from seleric_swarm.agent.model import resolve_v3_model
from seleric_swarm.agent.output import MissionResult as V3MissionResult
from seleric_swarm.agent.plan import build_plan
from seleric_swarm.agent.scope import (
    RequiredScope,
    ValueFilter,
    build_required_scope,
    question_axes_from_resolution,
    value_filters_from_resolution,
)
from seleric_swarm.agent.validation import run_validated_mission
from seleric_swarm.api.office.registry import register_mission
from seleric_swarm.api.office.v3_adapter import v3_raw_snapshot
from seleric_swarm.api.v3_state import get_v3_artifact_store, get_v3_mission_store
from seleric_swarm.contracts.lookup import (
    EvidenceView,
    MissionError,
    TraceInfo,
)
from seleric_swarm.contracts.lookup import (
    MissionResult as LookupMissionResult,
)
from seleric_swarm.conversations.contracts import (
    Artifact,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
    TurnRecord,
)
from seleric_swarm.observability.traces import mission_trace
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.services.metrics import MetricDefinition, MetricRegistry
from seleric_swarm.contracts.lookup import TimeRangeV1
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata
from seleric_swarm.services.time_range import as_of_date, window_from_query
from seleric_swarm.state.missions import Mission
from seleric_swarm.toolsets.semantic import query_metrics

_log = logging.getLogger("seleric.agent.runner")

_LOOKUP_STATUSES = {
    "completed",
    "partial",
    "failed",
    "running",
    "cancelled",
    "blocked",
    "prototype_completed",
}

_ALIAS_REGISTRY: MetricRegistry | None = None


def _alias_registry() -> MetricRegistry:
    global _ALIAS_REGISTRY
    if _ALIAS_REGISTRY is None:
        _ALIAS_REGISTRY = MetricRegistry("config/metric_registry.yaml")
    return _ALIAS_REGISTRY


def _lookup_alias(query: str) -> MetricDefinition | None:
    """Exact YAML overlay only (``ns``/``np``/``adsp``/``gs``).

    Lives on the runner, not ``query_metrics``: Profile B requires two
    spellings of one metric to stay independently attributed at the tool.
    """
    return _alias_registry().resolve_alias(query)


# --- Jev-driven routing (latency optimizations) --------------------------------

# Intents whose answers genuinely benefit from an upfront plan. Simple lookups
# already have the full catalogue + capability manifest in context.
_PLAN_INTENTS = frozenset(
    {"diagnostic", "causal_investigation", "simulation", "forecast", "comparison"}
)

# Per-intent tool-call ceilings. These sit under Settings.max_tool_calls.
# Unknown intent (Jev down) keeps the configured ceiling, never tightening
# on missing signal. Headroom is large on purpose: a lookup still does
# search→resolve→query→synthesis, and a diagnostic/causal run fans out.
_TOOL_BUDGET_BY_INTENT: dict[str, int] = {
    "conversation": 1,
    "lookup": 100,
    "aggregation": 100,
    "trend": 100,
    "comparison": 150,
    "diagnostic": 250,
    "forecast": 250,
    "simulation": 250,
    "causal_investigation": 250,
}

# Simple, read-only intents cheap enough for the fast model tier.
_FAST_MODEL_INTENTS = frozenset({"conversation", "lookup", "aggregation", "trend"})
# Intents answered by toolsets/diagnosis.py::diagnose_metric_change.
_DIAGNOSTIC_INTENTS = frozenset({"diagnostic", "causal_investigation"})


def _should_plan(classification: QueryClassification) -> bool:
    """#1: plan only for multi-step work (by intent) or when Jev rates it complex."""
    return classification.intent in _PLAN_INTENTS or classification.complexity == "complex"


def _tool_budget(intent: str | None, ceiling: int) -> int:
    """#3: per-intent tool-call budget, never above the configured ceiling."""
    if intent is None:
        return ceiling
    return min(ceiling, _TOOL_BUDGET_BY_INTENT.get(intent, ceiling))


def _prefer_fast_model(classification: QueryClassification) -> bool:
    """#3: route simple, read-only, non-complex queries to the fast model tier."""
    return (
        classification.intent in _FAST_MODEL_INTENTS
        and classification.complexity != "complex"
        and classification.needs_write is not True
    )


def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    """Unwrap ``BaseExceptionGroup`` (e.g. ``FallbackModel``'s all-candidates-failed
    group) into its leaf exceptions so failure classification can inspect the
    actual provider errors, not just the group's own summary message."""
    if isinstance(exc, BaseExceptionGroup):
        flat: list[BaseException] = []
        for sub in exc.exceptions:
            flat.extend(_flatten_exceptions(sub))
        return flat
    return [exc]


def _user_facing_agent_failure(exc: BaseException) -> tuple[str, str]:
    """Chat-safe failure text — never dump provider HTTP bodies to the UI.

    Classifies by exception type/attributes, not string content: a
    ``FallbackExceptionGroup`` from an exhausted model fallback chain carries
    no "429" in its own message, only in its wrapped sub-exceptions.
    """
    causes = _flatten_exceptions(exc)
    if any(
        (isinstance(c, ModelHTTPError) and c.status_code == 429)
        or "429" in str(c)
        or "ratelimit" in str(c).lower()
        for c in causes
    ):
        return (
            "The language model is rate-limited right now. Please retry in a moment.",
            "LLM_RATE_LIMITED",
        )
    if any(isinstance(c, OpenAIAPITimeoutError) or "timeout" in str(c).lower() for c in causes):
        return ("The agent timed out. Please retry.", "V3_AGENT_TIMEOUT")
    return (
        "The agent could not complete this question. Please retry.",
        "V3_AGENT_FAILED",
    )


def _as_of_datetime(as_of: str | None, timezone: str = "Asia/Kolkata") -> datetime:
    """Anchor as_of on the mission calendar day, not UTC-now (which can lag IST)."""
    day = as_of_date(as_of, timezone)
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        tz = UTC
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def _required_scope(
    runtime: SwarmRuntime, query: str, temporal_grain: str | None = None
) -> RequiredScope:
    """Resolve the query's requested breakdowns to catalogue dimension ids via
    the bootstrap's alias index. Fail-open: no bootstrap / any error ⇒ empty
    scope (the coverage check becomes NOT_APPLICABLE)."""
    bootstrap = getattr(runtime, "bootstrap", None)
    if bootstrap is None:
        return RequiredScope()
    try:
        return build_required_scope(
            query,
            alias_index=bootstrap.alias_index(),
            dimension_ids=bootstrap.dimension_ids(),
            temporal_grain=temporal_grain,
        )
    except Exception:
        _log.warning("required_scope_failed", exc_info=True)
        return RequiredScope()


# Per-process cache for value resolution (query -> result)
_VALUE_RESOLUTION_CACHE: dict[str, dict[str, Any]] = {}

async def _resolve_values(runtime: SwarmRuntime, mcp: Any, query: str) -> dict[str, Any]:
    """Map the question's words to values the data records, before the loop.

    The gateway learns every dimension's live values from Cube
    (``catalogue_resolve_values``), so "whatsapp" resolves to the utm_medium
    values the data actually uses without anyone declaring it. Fail-open: an
    error, timeout or a still-warming index yields no hints and the mission runs
    exactly as before."""
    # Check cache first
    cache_key = query.strip().lower()
    if cache_key in _VALUE_RESOLUTION_CACHE:
        return _VALUE_RESOLUTION_CACHE[cache_key]
    
    timeout = float(getattr(runtime.settings, "value_resolve_timeout_s", 6.0))
    try:
        result = await asyncio.wait_for(
            mcp.call(
                agent_id="v3_agent",
                capability="seleric.catalogue_resolve_values",
                arguments={"text": query},
            ),
            timeout=timeout,
        )
    except Exception:
        _log.warning("value_resolution_failed", exc_info=True)
        return {}
    if not isinstance(result, dict) or result.get("status") != "ok":
        return {}
    # Cache the result
    _VALUE_RESOLUTION_CACHE[cache_key] = result
    return result


_VALUE_SENSE_PROMPT = (
    "A business question was matched word-by-word against values recorded in the data. For each "
    "listed word, decide whether the question uses it to NAME that value (the user wants the data "
    "filtered to it, e.g. 'whatsapp' in 'orders from whatsapp', 'meta' in 'meta spend') or as "
    "ordinary language (e.g. 'other' in 'compared to other days', 'new' in 'any new ideas'). "
    "When unsure, the word names the value. Reply with JSON only: "
    '{"ordinary": ["<word>", ...]} listing only the ordinary-language words.'
)


async def _confirm_value_filters(
    runtime: SwarmRuntime, query: str, filters: tuple[ValueFilter, ...]
) -> tuple[tuple[ValueFilter, ...], frozenset[str]]:
    """Drop value filters whose word the question uses as plain language.

    An exact data match is not intent: "compared to OTHER days" matched
    payment_method = other, the coverage gate made it required, and the model —
    told the data records it — kept filtering by it (live thread_e75c2615, still
    after the not_values waiver). One fast-model call reads each word in context,
    before the loop. Fail-open toward the filter (an error keeps every filter, so
    a named "meta" is never dropped by an outage)."""
    llm = getattr(runtime, "llm", None)
    if not filters or llm is None:
        return filters, frozenset()
    settings = runtime.settings
    model = (getattr(settings, "azure_openai_fast_model", "") or "").strip() or settings.azure_openai_model
    listing = "\n".join(
        f'- "{vf.term}": {" / ".join(sorted(vf.dimensions))} = {", ".join(vf.values)}' for vf in filters
    )
    try:
        response = await asyncio.wait_for(
            llm.complete(
                LLMRequest(
                    messages=[
                        ChatMessage(role="system", content=_VALUE_SENSE_PROMPT),
                        ChatMessage(role="user", content=f"Question: {query}\n\nWords:\n{listing}"),
                    ],
                    model=model,
                    temperature=0,
                    max_tokens=1500,  # reasoning models spend hidden tokens first
                    timeout_s=8.0,
                    metadata=LLMRequestMetadata(agent_id="value_sense", query_class="scope"),
                    tags=["value_sense"],
                )
            ),
            timeout=10.0,
        )
        found = re.search(r"\{.*\}", response.text or "", re.DOTALL)
        words = json.loads(found.group(0)).get("ordinary", []) if found else []
    except Exception:
        _log.warning("value_sense_failed", exc_info=True)
        return filters, frozenset()
    named = {vf.term.lower() for vf in filters}
    ordinary = frozenset(str(w).strip().lower() for w in words if str(w).strip().lower() in named)
    if ordinary:
        _log.info("value_filters_dropped_as_ordinary terms=%s", sorted(ordinary))
    return tuple(vf for vf in filters if vf.term.lower() not in ordinary), ordinary


def _without_terms(resolution: dict[str, Any], terms: frozenset[str]) -> dict[str, Any]:
    if not terms or not resolution:
        return resolution
    kept = [t for t in resolution.get("terms") or [] if str(t.get("term", "")).lower() not in terms]
    return {**resolution, "terms": kept}


_VALUES_MAX_TERMS = 4
_VALUES_MAX_DIMENSIONS = 3
_VALUES_MAX_VALUES = 5


def _values_block(resolution: dict[str, Any]) -> str:
    """Render value matches for the model: exact matches as what the user
    meant, everything else as suggestions it may ignore."""
    lines: list[str] = []
    for term in (resolution.get("terms") or [])[:_VALUES_MAX_TERMS]:
        parts: list[str] = []
        for d in (term.get("dimensions") or [])[:_VALUES_MAX_DIMENSIONS]:
            values = ", ".join(
                f"{v.get('value')} ({v.get('match')})"
                for v in (d.get("values") or [])[:_VALUES_MAX_VALUES]
            )
            metrics = ", ".join(d.get("metrics") or [])
            parts.append(
                f"{d.get('dimension')} = {values}"
                + (f" [metrics with this dimension include: {metrics}]" if metrics else "")
            )
        if parts:
            lines.append(f'- "{term.get("term")}" → ' + "; ".join(parts))
    if not lines:
        return ""
    return (
        "[values in the data — words in the question that match values the data "
        "records, learned from live data. A match is a candidate: if the question "
        "uses a word as ordinary language (\"other days\", \"new idea\"), do not "
        "filter by it and list the word in not_values]\n"
        + "\n".join(lines)
        + "\n\n"
    )


async def _catalogue_snapshot(runtime: SwarmRuntime) -> CatalogueSnapshot:
    """Warm the live catalogue cache and snapshot it for the agent's memory.

    Fail-open: a warming failure leaves an empty snapshot, and the agent falls
    back to ``search_semantics`` exactly as before.
    """
    bootstrap = getattr(runtime, "bootstrap", None)
    if bootstrap is None:
        return CatalogueSnapshot()
    try:
        await bootstrap.refresh_if_stale()
        return bootstrap.snapshot()
    except Exception:
        _log.warning("catalogue_snapshot_failed", exc_info=True)
        return CatalogueSnapshot()


def _store_plan_artifact(deps: SelericDeps, *, plan: str, intent: str | None) -> None:
    """Persist the plan for observability. Non-fatal — a store failure never
    blocks the mission (the plan is already prepended to the prompt)."""
    try:
        deps.artifact_store.put(
            Artifact(
                workspace_id=deps.principal.workspace_id,
                artifact_type="plan",
                payload={"plan": plan, "intent": intent},
                classification="ui",
                mission_id=deps.mission_id,
            )
        )
    except Exception:
        _log.warning("plan_artifact_store_failed", exc_info=True)


def _resolved_window(query: str, timezone: str, as_of: str):
    """The single absolute period the question names, or None.

    Only a single absolute span is returned: a comparison legitimately spans two
    ranges, and a question naming no relative period has nothing to pin.
    """
    try:
        window = window_from_query(query, timezone, as_of)
    except Exception:
        return None
    if window is None or window.kind != "absolute":
        return None
    if not window.relative_token or not window.start or not window.end:
        return None
    return window


# A bare acceptance of the prior answer's closing offer ("yes", "sure, go ahead").
# Grammar words only — never metric or domain words.
_AFFIRMATION_WORDS = frozenset(
    "yes yeah yep yup sure ok okay go ahead please do it that proceed haan ha ji".split()
)
# Social turns: answered directly, no tools, no classifier round trip. "thanks"
# is here, not an acceptance — it used to re-run the previous answer's offer.
# Live 2026-10-05: Jev labelled "hi" as a trend AND a follow-up (it labels every
# message "trend"); in a thread the greeting kept its tools, the zero-tool gate
# rejected the greeting twice, and the user got "Net sales were ₹87,998.05".
_SMALL_TALK_WORDS = frozenset(
    "hi hii hiii hello helo hey heya hiya yo namaste hola there good morning afternoon "
    "evening night thanks thank you thx ty tysm cheers bye goodbye see later great cool "
    "nice awesome how are r u doing whats what's up sup seleric team ok okay".split()
)
_PERIOD_LINE = re.compile(
    r"Period:\s*(\d{4}-\d{2}-\d{2})(?:\s*(?:to|through|\.\.|–|—|-)\s*(\d{4}-\d{2}-\d{2}))?"
)


def _is_affirmation(query: str) -> bool:
    words = re.sub(r"[^a-z ]", " ", query.lower()).split()
    return 0 < len(words) <= 5 and all(w in _AFFIRMATION_WORDS for w in words)


def _is_small_talk(query: str) -> bool:
    if re.search(r"\d", query):
        return False
    words = re.sub(r"[^a-z' ]", " ", query.lower()).split()
    # A bare "ok"/"okay" accepts the previous offer; that stays a follow-up.
    return 0 < len(words) <= 6 and all(w in _SMALL_TALK_WORDS for w in words) and not _is_affirmation(query)


def _answer_period(final_response: str) -> tuple[str, str] | None:
    """The period the answer stated in its footer ("Period: A to B" / "A..B" / "A")."""
    found = _PERIOD_LINE.findall(final_response or "")
    if not found:
        return None
    start, end = found[-1]
    return start, end or start


def _closing_offer(final_response: str) -> str:
    """The question the answer ended on ("Would you like me to …?"), if any."""
    for line in reversed((final_response or "").strip().splitlines()):
        line = line.strip()
        if line.endswith("?"):
            return re.split(r"(?<=[.!])\s+", line)[-1][:300]
        if line:
            return ""
    return ""


def _names_a_period(query: str, timezone: str, as_of: str) -> bool:
    try:
        return window_from_query(query, timezone, as_of) is not None
    except Exception:
        return True  # unparseable phrase: do not impose the prior period


def _prior_window(record: dict[str, Any] | None):
    """The period the prior answer covered, as a pinned window for a follow-up that names none."""
    start, end = (record or {}).get("period_start"), (record or {}).get("period_end")
    if not start or not end:
        return None
    return TimeRangeV1(kind="absolute", start=start, end=end, relative_token="prior_answer_period")


def _followup_hint(record: dict[str, Any] | None, *, affirmation: bool, window: Any) -> str:
    """Follow-ups keep the prior answer's period, and "yes" means do what was offered.
    Live thread_e75c2615: "yes" to "dig into channel or landing-page behaviour?"
    re-ran the same diagnosis on a different window (-6% "normal" became -15%
    "notable") and never drilled by channel."""
    if not record:
        return ""
    lines: list[str] = []
    if window is not None and window.relative_token == "prior_answer_period":
        lines.append(
            f"The question names no period: use the prior answer's period {window.start}..{window.end} "
            "(pass those dates explicitly) unless the request itself implies another, e.g. a comparison."
        )
    offer = record.get("offer")
    if affirmation and offer:
        lines.append(
            f'The user accepted the offer that ended your prior answer: "{offer}" Do exactly that '
            "(e.g. the breakdown or drill it names), on the same metric and period; do not repeat the "
            "prior analysis."
        )
    return "[follow-up]\n" + "\n".join(lines) + "\n\n" if lines else ""


def _resolved_window_line(query: str, timezone: str, as_of: str) -> str:
    """Resolve a relative time phrase to concrete dates once, deterministically,
    so the agent uses a fixed window instead of resolving "last month" itself —
    which drifted (live L1 read it as the previous full month, L7 as the current
    partial month). Only relative phrases are pinned; explicit dates in the
    question don't drift, and an unresolved phrase adds nothing (fail-open)."""
    try:
        window = window_from_query(query, timezone, as_of)
    except Exception:
        return ""
    if window is None or not window.relative_token or not window.start:
        return ""
    if window.kind == "comparison" and window.start_b and window.end_b:
        return (
            f"'{window.relative_token.replace('_', ' ')}' resolves to "
            f"{window.start}..{window.end} vs {window.start_b}..{window.end_b}. "
        )
    span = window.start if window.end in (None, window.start) else f"{window.start} through {window.end}"
    return f"'{window.relative_token.replace('_', ' ')}' resolves to {span}. "


def _routing_hint(classification: QueryClassification) -> str:
    """Render the Jev per-query signals as an advisory hint. Only signals that
    carry information are shown (a ``none``/``either``/``False`` answer is noise).
    Advisory: the agent must ignore any hint the question text contradicts, and
    Jev never supplies dates — ``period=custom_date_range`` means the user gave
    explicit dates the agent should read from the question itself."""
    parts: list[str] = []
    if classification.grain and classification.grain != "none":
        parts.append(f"grain={classification.grain}")
    if classification.period and classification.period != "none":
        parts.append(f"period={classification.period}")
    if classification.direction and classification.direction != "either":
        parts.append(f"direction={classification.direction}")
    if classification.depends_on_prior:
        parts.append("follow_up=true")
    why = (
        "[why-question: resolve the metric once, then call diagnose_metric_change for the period asked "
        "about and answer from its ANSWER SKELETON — do not reconstruct the diagnosis from query_metrics]\n\n"
        if classification.intent in _DIAGNOSTIC_INTENTS
        else ""
    )
    if not parts:
        return why
    return why + (
        "[routing hint (advisory — defer to the question if it disagrees; "
        "for period=custom_date_range use the explicit dates in the question, "
        f"never invent dates): {' '.join(parts)}]\n\n"
    )


# ── Context rendering constants ────────────────────────────────────────────────
# Follow-up turns get more history; non-follow-ups only need recent context.
_MAX_TURNS_FOLLOWUP = 6       # (user + assistant) pairs for follow-up queries
_MAX_TURNS_DEFAULT = 3        # (user + assistant) pairs for standalone queries
# Tiered character limits per turn — the most recent assistant answer is the
# most likely reference point for a follow-up, so it gets the largest budget.
_CHARS_LAST_ASSISTANT = 800   # last Seleric response (most grounding-critical)
_CHARS_OLDER_ASSISTANT = 180  # older Seleric responses (reference only)
_CHARS_USER_TURN = 300        # any user message
_MAX_MEMORIES = 5             # max active memories injected
_CHARS_PER_MEMORY = 200       # char limit per memory item
_MAX_ENTITIES_FROM_RECORD = 8 # max entity names from TurnRecord in the prompt


def _parse_named_entities(text: str) -> list[str]:
    """Extract named entity strings from Markdown bullet/table output.

    Only captures text in bullet lines ("- Name:") or table cells ("| Name |").
    Returns at most 15 items, deduplicated, never empty strings.
    Labels that are obviously column headers or footer lines are excluded.

    This is conservative by design: false positives (adding noise to context)
    are more harmful than false negatives (missing an entity).
    """
    import re
    _BULLET = re.compile(r"^[-*]\s+([^:\n|]{2,40}):", re.MULTILINE)
    _TABLE = re.compile(r"\|\s*([^|\n]{2,40?})\s*\|", re.MULTILINE)
    # Patterns that indicate a footer/header row — skip these
    _SKIP = re.compile(
        r"^(period|currency|data|metric|total|---|\.{3})",
        re.IGNORECASE,
    )
    found: list[str] = []
    for m in _BULLET.finditer(text):
        name = m.group(1).strip()
        if name and not _SKIP.match(name):
            found.append(name)
    for m in _TABLE.finditer(text):
        name = m.group(1).strip()
        if name and not name.startswith(("-", "=", " ")) and not _SKIP.match(name):
            found.append(name)
    # Deduplicate while preserving order
    return list(dict.fromkeys(found))[:15]


def _render_turn_record(record: dict[str, Any]) -> str:
    """Render a TurnRecord payload as a single compact labelled line.

    Format: "Prior answer: <intent> | period=<p> | grain=<g> | entities=[e1, e2] | metrics=[m1]"
    Only non-empty, non-'none' fields are included. The result is injected at
    the very top of [thread context] — before memories and raw turns — so the
    agent reads it first (primacy effect).
    """
    parts: list[str] = []
    if record.get("intent"):
        parts.append(record["intent"])
    period = record.get("period")
    if period and period != "none":
        parts.append(f"period={period}")
    if record.get("period_start"):
        parts.append(f"period_used={record['period_start']}..{record.get('period_end') or record['period_start']}")
    grain = record.get("grain")
    if grain and grain != "none":
        parts.append(f"grain={grain}")
    entities = (record.get("entities") or [])[:_MAX_ENTITIES_FROM_RECORD]
    if entities:
        parts.append(f"entities=[{', '.join(entities)}]")
    # Use human-readable labels; never expose internal metric IDs
    labels = (record.get("metric_labels") or record.get("top_items") or [])[:4]
    if labels:
        parts.append(f"metrics=[{', '.join(labels)}]")
    as_of = record.get("as_of")
    if as_of:
        parts.append(f"as_of={as_of}")
    return "Prior answer: " + " | ".join(parts) if parts else ""


def _thread_context_block(
    bundle: ContextBundle | None,
    *,
    is_followup: bool = False,
    prior_turn_record: dict[str, Any] | None = None,
) -> str:
    """Compose the [thread context] block from structured, labelled sources.

    Injection order (primacy → recency, most important first):
      1. Prior answer TurnRecord — structured facts from the last turn.
         This is the primary grounding source for follow-up resolution.
         Never use raw prose from the last assistant message for this purpose.
      2. Active memories (PREFERENCE, CONSTRAINT, DEFINITION) — per-user or
         per-thread facts the agent must respect across all answers.
      3. Recent turns — last N (user, Seleric) pairs. Tiered char limits:
         - Last Seleric response: _CHARS_LAST_ASSISTANT (most referenced)
         - Older Seleric responses: _CHARS_OLDER_ASSISTANT (reference only)
         - Any user message: _CHARS_USER_TURN
      4. Thread summary — fallback only when there are no recent turns and
         no TurnRecord. Raw summary prose is a weak signal; prefer structured
         sources whenever available.

    Content policy — what must NEVER appear in the returned string:
      - Internal artifact IDs, mission IDs, run IDs
      - Raw tool call JSON or Cube response bodies
      - Internal metric IDs (e.g. "metric.shopify_ns_v2")
      - Numbers without an associated metric label
      - Full Markdown tables (entity names only, not the table itself)
    """
    if bundle is None:
        return ""

    lines: list[str] = []
    max_turns = _MAX_TURNS_FOLLOWUP if is_followup else _MAX_TURNS_DEFAULT

    # ── 1. Prior TurnRecord (highest-priority grounding signal) ──────────────
    if prior_turn_record:
        rendered = _render_turn_record(prior_turn_record)
        if rendered:
            lines.append(rendered)

    # ── 2. Active memories (preferences, constraints, definitions) ─────────
    for mem in bundle.memories[:_MAX_MEMORIES]:
        content = mem.content if isinstance(mem.content, str) else str(mem.content)
        text = content.strip()[:_CHARS_PER_MEMORY]
        if not text:
            continue
        kind = getattr(mem.type, "value", str(mem.type))
        lines.append(f"Memory ({kind}): {text}")

    # ── 3. Recent turns — tiered character budgets ─────────────────────
    raw_messages = bundle.recent_messages[-(max_turns * 2):]
    turns: list[str] = []
    n = len(raw_messages)
    for i, raw in enumerate(raw_messages):
        message = raw if isinstance(raw, dict) else {}
        text = _part_text(message)
        if not text:
            continue
        role_str = str(message.get("role") or "").upper()
        who = "User" if role_str == "USER" else "Seleric"
        is_last_assistant = (who == "Seleric" and i == n - 1)
        if is_last_assistant:
            char_limit = _CHARS_LAST_ASSISTANT
        elif who == "User":
            char_limit = _CHARS_USER_TURN
        else:
            char_limit = _CHARS_OLDER_ASSISTANT
        turns.append(f"{who}: {text[:char_limit]}")

    if turns:
        lines.append("Recent turns:")
        lines.extend(turns)

    # ── 4. Summary fallback (only when no turns and no TurnRecord) ────────
    if not turns and not prior_turn_record:
        summary_text = bundle.latest_summary.summary.strip() if bundle.latest_summary else ""
        if summary_text:
            lines.append(f"Summary: {summary_text[:600]}")

    if not lines:
        return ""
    return "[thread context]\n" + "\n".join(lines) + "\n\n"


def _part_text(message: dict[str, Any]) -> str:
    """Extract concatenated text content from a Message dict's parts list."""
    chunks: list[str] = []
    for part in message.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "").upper() != "TEXT":
            continue
        content = part.get("content")
        if isinstance(content, str) and content.strip():
            chunks.append(content.strip())
    return "\n".join(chunks)


def _mission_prompt(
    query: str,
    as_of_dt: datetime,
    timezone: str,
    context: ContextBundle | None = None,
    catalogue: CatalogueSnapshot | None = None,
    plan: str | None = None,
    hint: str = "",
    is_followup: bool = False,
    prior_turn_record: dict[str, Any] | None = None,
) -> str:
    """Assemble the full user-turn prompt for one mission.

    The [thread context] block is placed first so the model reads prior-answer
    grounding before anything else (primacy effect). For follow-up queries,
    this block leads with a structured 'Prior answer:' line from the last
    TurnRecord, not raw prose from the previous assistant message.
    """
    as_of_day = as_of_dt.date()
    yesterday = as_of_day.fromordinal(as_of_day.toordinal() - 1)
    window_line = _resolved_window_line(query, timezone, as_of_day.isoformat())
    thread = _thread_context_block(
        context,
        is_followup=is_followup,
        prior_turn_record=prior_turn_record,
    )
    catalogue_block = ""
    if catalogue is not None:
        rendered = catalogue.render()
        if rendered:
            catalogue_block = f"[catalogue]\n{rendered}\n\n"
    plan_block = f"[plan]\n{plan}\n\n" if plan else ""
    return (
        f"{thread}{catalogue_block}{plan_block}{hint}{query}\n\n"
        f"[system: mission as_of={as_of_day.isoformat()} timezone={timezone}. "
        f"'today' is {as_of_day.isoformat()}. "
        f"'yesterday' is {yesterday.isoformat()}. "
        f"{window_line}"
        f"Use only this calendar; do not invent another year from training data. "
        f"Use thread context for follow-ups; do not treat this as a brand-new chat.]"
    )


def _context_bundle(raw: dict[str, Any] | None) -> ContextBundle:

    if not raw:
        return ContextBundle()
    try:
        return ContextBundle.model_validate(raw)
    except Exception:
        _log.warning("context_bundle_validation_failed", exc_info=True)
        return ContextBundle()


def _principal(*, workspace_id: str, user_id: str) -> Principal:
    return Principal(
        principal_id=uuid4().hex,
        workspace_id=workspace_id or "default",
        user_id=user_id or "default",
        authenticated=False,
        auth_method=PrincipalAuthMethod.ANONYMOUS,
    )


def _lookup_status(status: str) -> str:
    """Map a MissionResult status onto the stored mission status.

    Only reached from ``_to_lookup`` on the terminal persistence path, so a
    non-terminal status must not survive: live, a ``final_result`` call with
    status="running" was written verbatim and left the missions row stuck at
    'running' forever while the run reported COMPLETED.
    """
    if not is_terminal_status(status):
        return "partial"
    return status if status in _LOOKUP_STATUSES else "partial"


class _ToolCtx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _format_amount(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value).is_integer() or abs(value) >= 100:
        return f"{value:,.0f}"
    return f"{value:,.4g}"


async def _alias_lookup_result(
    deps: SelericDeps,
    *,
    query: str,
    definition: Any,
    request_id: str,
    thread_id: str,
    intent: str | None = None,
) -> V3MissionResult:
    """Live Cube lookup for an exact YAML alias — no LLM, no invented metric id."""
    catalogue_id = str(definition.catalogue_metric or definition.id.removeprefix("metric."))
    catalogue_filters = dict(getattr(definition, "catalogue_filters", None) or {})
    tool = await query_metrics(
        _ToolCtx(deps), metric_id=catalogue_id, dimensions=catalogue_filters or None, pool_listed_values=True
    )
    value = None
    if tool.artifact_ids:
        artifact = deps.artifact_store.get(tool.artifact_ids[0])
        if artifact is not None and isinstance(artifact.payload, dict):
            value = artifact.payload.get("value")
    label = next(iter(definition.aliases), definition.id.removeprefix("metric."))
    unit = str(getattr(definition, "unit", "") or "").strip()
    if tool.success and value is not None:
        amount = _format_amount(value)
        answer = f"{label}: {amount} {unit}".strip()
        status = "completed"
        error_code = None
    else:
        answer = tool.summary or f"No live data for {label}."
        status = "failed"
        error_code = tool.error_code or "INSUFFICIENT_EVIDENCE"
    return V3MissionResult(
        mission_id=deps.mission_id,
        status=status,
        query=query,
        as_of=deps.as_of,
        final_response=answer,
        evidence_ids=list(tool.artifact_ids),
        limitations=[] if tool.success else [error_code or "INSUFFICIENT_EVIDENCE"],
        error_code=error_code,
        trace={
            "request_id": request_id,
            "session_id": thread_id,
            "lookup": "alias",
            "intent": intent,
        },
    )


# Broad "how's the business" overviews the ready-store fast path can answer from
# a pre-computed snapshot. Kept deliberately narrow (whole-question overviews, no
# specific metric) so a targeted lookup never gets hijacked onto the 10-metric
# headline snapshot. Stale/missing snapshot -> None -> normal agent loop.
_HIGH_LEVEL_PHRASES = (
    "how's the business",
    "how is the business",
    "how's business",
    "how is business",
    "how are we doing",
    "how are we performing",
    "how's it going",
    "how's the company",
    "state of the business",
    "business overview",
    "business health",
    "overall performance",
    "give me an overview",
    "how are things",
)


def _is_high_level_business_query(query: str) -> bool:
    if os.getenv("BUSINESS_STATE_FAST_PATH", "1").strip() in {"0", "false", "no"}:
        return False
    norm = re.sub(r"[^a-z0-9\s']", " ", (query or "").lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm or len(norm.split()) > 8:
        return False
    return any(phrase in norm for phrase in _HIGH_LEVEL_PHRASES)


async def _business_state_fast_answer(
    runtime: SwarmRuntime,
    *,
    deps: SelericDeps,
    query: str,
    request_id: str,
    thread_id: str,
    intent: str | None,
) -> V3MissionResult | None:
    """Answer a high-level query from the ready-store snapshot in one LLM call.
    Returns None (→ fall back to the agent loop) when the snapshot is missing,
    UNAVAILABLE, or stale."""
    from seleric_swarm.services.business_state.formatter import format_business_state, is_stale
    from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

    try:
        snapshot = await SnapshotStore().aget_latest("business")
        if snapshot is None or snapshot.status == "UNAVAILABLE" or is_stale(snapshot):
            return None
        answer = await format_business_state(
            runtime, question=query, snapshot=snapshot, request_id=request_id, session_id=thread_id
        )
    except Exception:
        _log.warning("business_state_fast_path_failed", exc_info=True)
        return None
    return V3MissionResult(
        mission_id=deps.mission_id,
        status="completed",
        query=query,
        as_of=deps.as_of,
        final_response=answer,
        trace={
            "request_id": request_id,
            "session_id": thread_id,
            "lookup": "business_state_fast_path",
            "intent": intent,
            "as_of": snapshot.as_of,
        },
    )


def _to_lookup(
    result: V3MissionResult,
    *,
    request_id: str,
    session_id: str,
    evidence: list[EvidenceView],
) -> LookupMissionResult:
    error = None
    if result.error_code:
        error = MissionError(code=result.error_code, message=result.final_response or result.error_code)
    intent = result.trace.get("intent")
    return LookupMissionResult(
        mission_id=result.mission_id,
        status=_lookup_status(result.status),  # type: ignore[arg-type]
        query_class=str(intent) if intent else None,
        mission_lead="coordinator",
        initial_mission_lead="coordinator",
        evidence=evidence,
        limitations=list(result.limitations),
        final_response=result.final_response,
        error=error,
        trace=TraceInfo(
            request_id=str(result.trace.get("request_id") or request_id),
            session_id=str(result.trace.get("session_id") or session_id),
            elapsed_seconds=result.trace.get("elapsed_seconds"),
            steps=result.trace.get("steps"),
            validation=result.trace.get("validation"),
            intent=result.trace.get("intent"),
        ),
    )


def _latest_turn_record(
    artifact_store: Any,
    *,
    thread_id: str,
) -> dict[str, Any] | None:
    """Return the most recent 'turn_record' artifact payload for the given thread.

    Iterates artifacts newest-first using list_for_context. Returns the payload
    dict of the first turn_record found, or None if none exists or on any error.
    Callers must be prepared for None (graceful degradation to raw-message context).
    """
    list_fn = getattr(artifact_store, "list_for_context", None)
    if not callable(list_fn):
        return None
    try:
        for artifact in list_fn("", thread_id):
            if getattr(artifact, "artifact_type", None) == "turn_record":
                payload = getattr(artifact, "payload", None)
                if isinstance(payload, dict):
                    return payload
    except Exception:
        _log.warning("latest_turn_record_list_failed", exc_info=True)
    return None


def _write_turn_record(
    *,
    result: V3MissionResult,
    classification: QueryClassification,
    artifact_store: Any,
    thread_id: str,
    workspace_id: str,
    mission_id: str,
    as_of_dt: datetime,
) -> None:
    """Persist a TurnRecord artifact so the next turn can use it for grounding.

    Only writes for completed/partial missions with analytical intent.
    Conversation (small-talk) turns are skipped — they carry no metric facts.
    Any storage failure is logged and swallowed; a missing TurnRecord causes
    graceful degradation, not a mission failure.

    Content policy enforced here:
      - metric_labels: human-readable labels from evidence artifacts
      - entities: named items extracted from Markdown output (bullets/tables)
      - No internal IDs, no raw numbers, no full prose text
    """
    if result.status not in {"completed", "partial"}:
        return
    if classification.intent == "conversation":
        return
    try:
        entities = _parse_named_entities(result.final_response or "")
        # Extract metric labels from evidence artifacts for follow-up grounding
        metric_labels: list[str] = []
        for eid in (result.evidence_ids or [])[:8]:
            artifact = artifact_store.get(eid)
            if artifact and artifact.artifact_type == "evidence":
                try:
                    from seleric_swarm.agent.artifacts import EvidenceArtifact
                    evidence = EvidenceArtifact.model_validate(artifact.payload)
                    if evidence.metric_id:
                        # Use the metric_id as label (could be enhanced with display_name from catalogue)
                        label = evidence.metric_id.removeprefix("metric.").replace("_", " ")
                        if label not in metric_labels:
                            metric_labels.append(label)
                except Exception:
                    pass
        record = {
            "query": result.query if isinstance(result.query, str) else str(result.query),
            "intent": classification.intent,
            "period": classification.period,
            "grain": classification.grain,
            "entities": entities[:10],
            "metric_labels": metric_labels[:10],
            "top_items": entities[:5],
            "evidence_ids": (result.evidence_ids or [])[:8],
            "offer": _closing_offer(result.final_response or ""),
            "mission_id": mission_id,
            "as_of": as_of_dt.date().isoformat(),
        }
        period = _answer_period(result.final_response or "")
        if period:
            record["period_start"], record["period_end"] = period
        artifact_store.put(
            Artifact(
                workspace_id=workspace_id,
                artifact_type="turn_record",
                payload={k: v for k, v in record.items() if v is not None and v != [] and v != ""},
                classification="ui",
                mission_id=mission_id,
                thread_id=thread_id,
            )
        )
    except Exception:
        _log.warning("turn_record_write_failed", exc_info=True)


async def run_v3_mission(

    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    mission_id: str | None = None,
    context_bundle: dict[str, Any] | None = None,
    workspace_id: str | None = None,
    owner_user_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    on_stream: Callable[[str, str], None] | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Execute the V3 agent and persist for conversations + Office UI.

    Extra kwargs (``full_diagnostic`` etc.) are accepted and ignored so
    ``run_mission_job`` / ``create_mission`` can call this with their full
    historical argument set after the swarm_v2 dispatcher was deleted.
    """
    mission_id = mission_id or f"MS3-{uuid4().hex[:10]}"
    request_id = request_id or uuid4().hex
    thread_id = thread_id or session_id or uuid4().hex
    run_id = run_id or request_id
    workspace_id = workspace_id or getattr(runtime.settings, "default_workspace_id", "default")
    owner_user_id = owner_user_id or getattr(runtime.settings, "default_user_id", "default")
    as_of_dt = _as_of_datetime(as_of, timezone)
    register_mission(mission_id)

    v3_store = get_v3_mission_store()
    if v3_store.get(mission_id) is None:
        v3_store.create(
            Mission(
                mission_id=mission_id,
                query=query,
                as_of=as_of_dt,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                thread_id=thread_id,
                run_id=run_id,
            )
        )

    mcp = getattr(runtime, "mcp", None) or NullMcpClient()
    small_talk = _is_small_talk(query)
    # Jev answers in ~6s (measured 2026-10-05) and was awaited before anything
    # else; it now runs alongside the catalogue / value resolution it never
    # depended on. Small talk needs neither.
    classifying = (
        None
        if small_talk
        else asyncio.ensure_future(
            classify_query(
                query,
                base_url=getattr(runtime.settings, "jev_base_url", ""),
                api_key=getattr(runtime.settings, "jev_api_key", ""),
                timeout=float(getattr(runtime.settings, "jev_timeout_s", 5.0)),
            )
        )
    )
    catalogue = await _catalogue_snapshot(runtime)
    values = {} if small_talk else await _resolve_values(runtime, mcp, query)
    classification = (
        QueryClassification(intent="conversation", depends_on_prior=False)
        if classifying is None
        else await classifying
    )
    if (grain := stated_grain(query)) and grain != classification.grain:
        classification = dataclasses.replace(classification, grain=grain)
    intent = classification.intent
    if intent == "conversation":
        values = {}
    required_scope = _required_scope(runtime, query, temporal_grain=classification.grain)
    value_filters, ordinary_words = await _confirm_value_filters(
        runtime, query, value_filters_from_resolution(values)
    )
    values = _without_terms(values, ordinary_words)
    question_axes = question_axes_from_resolution(values)
    if value_filters or question_axes:
        required_scope = dataclasses.replace(
            required_scope, value_filters=value_filters, question_axes=question_axes
        )
    # Detect a follow-up and load the last turn's grounding record. Jev's
    # depends_on_prior misses bare acceptances ("yes"), so those always count.
    affirmation = _is_affirmation(query)
    is_followup = not small_talk and (classification.depends_on_prior is True or affirmation)
    prior_turn_record: dict[str, Any] | None = None
    # Loaded for every analytical turn, not only flagged follow-ups: Jev called
    # "Now can you give me a breakdown by the ad, gross and net sale" (right
    # after a last-7-days report) standalone, and the agent picked 2026-09-07..
    # 10-04 on its own (live 2026-10-05 MS3-a93b7a3eb0). A thread's next
    # question that names no period continues the prior one by default.
    if not small_talk:
        try:
            prior_turn_record = _latest_turn_record(get_v3_artifact_store(), thread_id=thread_id)
        except Exception:
            _log.warning("prior_turn_record_load_failed", exc_info=True)
    resolved_window = _resolved_window(query, timezone, as_of_dt.date().isoformat())
    # The prior period is a stated default, not a pin: resolved_window rewrites every
    # call to its dates, which would break "compare with the week before".
    prior_window = None
    if not small_talk and not _names_a_period(query, timezone, as_of_dt.date().isoformat()):
        prior_window = _prior_window(prior_turn_record)
    ceiling = int(getattr(runtime.settings, "max_tool_calls", 160))
    deps = SelericDeps(
        mission_id=mission_id,
        as_of=as_of_dt,
        principal=_principal(workspace_id=workspace_id, user_id=owner_user_id),
        thread_id=thread_id,
        run_id=run_id,
        trace_id=request_id,
        context=_context_bundle(context_bundle),
        mcp_client=mcp,
        artifact_store=get_v3_artifact_store(),
        limits=ExecutionLimits(
            max_tool_calls=_tool_budget(intent, ceiling),
            max_runtime_seconds=float(getattr(runtime.settings, "mission_timeout_s", 600.0)),
            agent_retries=int(getattr(runtime.settings, "agent_retries", 2)),
            max_validation_revisions=int(
                getattr(runtime.settings, "max_validation_revisions", 3)
            ),
            mcp_call_timeout_s=float(getattr(runtime.settings, "mcp_call_timeout_s", 15.0)),
        ),
        catalogue=catalogue,
        metrics=getattr(runtime, "metrics", None),
        resolved_window=resolved_window,
        jev=JevConfig(
            base_url=getattr(runtime.settings, "jev_base_url", ""),
            api_key=getattr(runtime.settings, "jev_api_key", ""),
            timeout=float(getattr(runtime.settings, "jev_timeout_s", 1.0)),
        ),
        required_scope=required_scope,
    )
    # An exact alias ("ns", "mer") is a metric name however short; the classifier
    # can read it as small talk or trend (live: Jev classifies "ns" as "trend"),
    # so the alias check runs for every intent. A verified YAML alias always takes
    # the deterministic Cube path; no model needed.
    alias_def = _lookup_alias(query)
    # Small talk gets no tools at all. Exception: an elliptical follow-up
    # (depends_on_prior) continues a prior analytical turn — "yes" confirming a
    # pending breakdown, "and for brand X?" — and stripping its tools made the
    # continuation impossible (live 2026-09-30, thread_14d713b4: "yes" narrated
    # "Running queries..." through final_result and failed
    # INSUFFICIENT_EVIDENCE). Tool availability is not tool invocation: true
    # small talk can still be answered without calling anything.
    if intent == "conversation" and alias_def is None and not is_followup:
        deps.call_counts[CONVERSATIONAL] = 1  # small talk: the agent gets no tools at all
    started = time.perf_counter()
    with mission_trace(
        mission_id,
        query=query,
        route="v3",
        intent=intent,
        complexity=classification.complexity,
        needs_write=classification.needs_write,
        request_id=request_id,
        session_id=thread_id,
        thread_id=thread_id,
        workspace_id=workspace_id,
        user_id=owner_user_id,
    ) as m_trace:
        try:
            fast_result = (
                await _business_state_fast_answer(
                    runtime, deps=deps, query=query, request_id=request_id,
                    thread_id=thread_id, intent=intent,
                )
                if alias_def is None and _is_high_level_business_query(query)
                else None
            )
            if fast_result is not None:
                result = fast_result
            elif alias_def is not None:
                result = await _alias_lookup_result(
                    deps,
                    query=query,
                    definition=alias_def,
                    request_id=request_id,
                    thread_id=thread_id,
                    intent=intent,
                )
            else:
                model = resolve_v3_model(
                    runtime.settings, prefer_fast=_prefer_fast_model(classification)
                )
                agent = build_seleric_agent(model=model)
                # #1: only pay for an upfront plan on genuinely multi-step work;
                # a simple lookup already has the full catalogue + manifest.
                plan = (
                    await build_plan(
                        model,
                        query=query,
                        intent=intent,
                        manifest=capability_manifest(),
                        catalogue=catalogue,
                    )
                    if _should_plan(classification)
                    else None
                )
                if plan:
                    _store_plan_artifact(deps, plan=plan, intent=intent)
                # prior_turn_record / is_followup were loaded before deps (the
                # prior period feeds the [follow-up] hint). The record sits at the top of
                # [thread context] as a structured 'Prior answer:' line.
                # Only pay the ~8.6k-token full-catalogue dump when explicitly
                # enabled; otherwise the agent resolves via search_semantics +
                # get_metric_definitions. The snapshot still rides in deps for
                # id validation (_reject_unknown_metric) either way.
                prompt = _mission_prompt(
                    query,
                    as_of_dt,
                    timezone,
                    context=deps.context,
                    catalogue=catalogue
                    if getattr(runtime.settings, "catalogue_in_prompt", False)
                    else None,
                    plan=plan,
                    hint=_values_block(values)
                    + _routing_hint(classification)
                    + _followup_hint(prior_turn_record, affirmation=affirmation, window=prior_window),
                    is_followup=is_followup,
                    prior_turn_record=prior_turn_record,
                )
                v3_result = await asyncio.wait_for(
                    run_validated_mission(agent, deps, prompt, on_stream=on_stream),
                    timeout=deps.limits.max_runtime_seconds,
                )
                result = v3_result.model_copy(
                    update={
                        "mission_id": mission_id,
                        "query": query,
                        "as_of": as_of_dt,
                        "trace": {
                            "request_id": request_id,
                            "session_id": thread_id,
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                            "intent": intent,
                            "complexity": classification.complexity,
                            "needs_write": classification.needs_write,
                            "grain": classification.grain,
                            "period": classification.period,
                            "direction": classification.direction,
                            "depends_on_prior": classification.depends_on_prior,
                            "steps": v3_result.trace.get("steps"),
                            # Verdict + every rejected revision's reason. Dropped
                            # here before 2026-10-04, so a mission that revised to
                            # exhaustion could only be diagnosed by replaying the
                            # validator by hand.
                            "validation": v3_result.trace.get("validation"),
                        },
                    }
                )
        except TimeoutError:
            result = V3MissionResult(
                mission_id=mission_id,
                status="failed",
                query=query,
                as_of=as_of_dt,
                final_response="The agent took too long to answer. Please retry.",
                limitations=["EXECUTION_LIMIT_EXCEEDED"],
                error_code="EXECUTION_LIMIT_EXCEEDED",
                trace={
                    "request_id": request_id,
                    "session_id": thread_id,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "intent": intent,
                },
            )
        except Exception as exc:
            message, error_code = _user_facing_agent_failure(exc)
            _log.warning(
                "v3_agent_failed",
                exc_info=True,
                extra={"mission_id": mission_id, "error_code": error_code, "intent": intent},
            )
            result = V3MissionResult(
                mission_id=mission_id,
                status="failed",
                query=query,
                as_of=as_of_dt,
                final_response=message,
                limitations=[error_code],
                error_code=error_code,
                trace={
                    "request_id": request_id,
                    "session_id": thread_id,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "intent": intent,
                },
            )
        finally:
            if "result" in locals() and hasattr(result, "final_response") and hasattr(m_trace, "set_output"):
                try:
                    m_trace.set_output(result.final_response)
                except Exception:
                    pass

    v3_store.finish(
        mission_id,
        status=result.status if result.status in {"completed", "partial", "failed", "cancelled"} else "partial",
        final_response=result.final_response,
        error_code=result.error_code,
    )

    # Write a TurnRecord artifact for grounding future follow-ups.
    # This runs after v3_store.finish so a storage failure never blocks the
    # mission result. It is non-fatal: a missing TurnRecord degrades gracefully
    # to the old raw-message context path (no crash, no user-visible error).
    _write_turn_record(
        result=result,
        classification=classification,
        artifact_store=get_v3_artifact_store(),
        thread_id=thread_id,
        workspace_id=workspace_id,
        mission_id=mission_id,
        as_of_dt=as_of_dt,
    )

    raw = v3_raw_snapshot(mission_id) or {}
    evidence_views = [
        EvidenceView.model_validate(row)
        for row in (raw.get("evidence") or [])
        if isinstance(row, dict)
    ]
    lookup = _to_lookup(
        result, request_id=request_id, session_id=thread_id, evidence=evidence_views
    )
    runtime.store.put(
        lookup,
        {
            **raw,
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "thread_id": thread_id,
            "run_id": run_id,
        },
    )
    return {"route": "v3", "result": lookup.model_dump()}

"""Query intake — normalize intents, metrics, entities, and time ranges."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seleric_swarm.coordinator.contracts import (
    EntityRef,
    NormalizedQuery,
    TimeRange,
)
from seleric_swarm.services.metrics import MetricRegistry

_DIAGNOSTIC_RE = re.compile(
    r"\b(why|root cause|reason for|caused?|explain|driver of|driving|drove|diagnose|"
    r"what changed|change in|drop in|fall in|decline in|increase in|rise in|spike in|"
    r"what.s behind|attributed to)\b",
    re.IGNORECASE,
)
_PREDICTIVE_RE = re.compile(
    r"\b(forecast|predict|what happens|if this continues|next week|projection)\b",
    re.IGNORECASE,
)
_PRESCRIPTIVE_RE = re.compile(
    r"\b(what should|recommend|what do we do|how do we fix|action)\b", re.IGNORECASE
)
_COMPARISON_RE = re.compile(r"\b(compare|versus| vs |against|difference)\b", re.IGNORECASE)
_LOOKUP_RE = re.compile(
    r"\b(what were|what was|what is|how much|how many|show me|tell me|get me|sales yesterday)\b",
    re.IGNORECASE,
)
_HEALTH_RE = re.compile(r"\b(how are we doing|how is (the )?business|health check)\b", re.IGNORECASE)

_METRIC_ALIASES: dict[str, str] = {
    "cac": "metric.cac",
    "blended cac": "metric.cac",
    "paid cac": "metric.cac",
    "cpm": "metric.cpm",
    "ctr": "metric.ctr",
    "cpc": "metric.cpc",
    "net sales": "metric.net_sales",
    "sales": "metric.net_sales",
    "shopify sales": "metric.net_sales",
    "orders": "metric.orders",
    "purchase cvr": "metric.purchase_cvr",
    "conversion": "metric.purchase_cvr",
    "cvr": "metric.purchase_cvr",
    "revenue": "metric.net_sales",
    "gross sales": "metric.gross_sales",
    "gross revenue": "metric.gross_sales",
    "gross profit": "metric.net_profit",
    "net profit": "metric.net_profit",
    "profit": "metric.net_profit",
    "margin": "metric.net_profit",
    "units sold": "metric.units_sold",
    "units": "metric.units_sold",
    "sessions": "metric.sessions",
    "traffic": "metric.sessions",
    "spend": "metric.spend",
    "ad spend": "metric.spend",
    "return rate": "metric.return_rate",
    "returns": "metric.return_rate",
    "refund": "metric.refunded_amount_excl_tax",
    "refunds": "metric.refunded_amount_excl_tax",
    "repeat rate": "metric.repeat_rate",
    "checkout rate": "metric.checkout_rate",
    "add to cart": "metric.atc_rate",
    "atc": "metric.atc_rate",
    "error rate": "metric.js_error_rate",
    "js error": "metric.js_error_rate",
    "api error": "metric.api_error_rate",
    "page load": "metric.mobile_lcp_seconds",
    "lcp": "metric.mobile_lcp_seconds",
}

_ENTITY_PATTERNS: list[tuple[str, str]] = [
    (r"\bmobile\b", "device"),
    (r"\bdesktop\b", "device"),
    (r"\bmeta\b|\bfacebook\b", "channel"),
    (r"\bgoogle\b", "channel"),
    (r"\bcheckout\b", "funnel_stage"),
    (r"\bpurchase\b", "funnel_stage"),
]


_ANALYTICAL_RES = (
    _DIAGNOSTIC_RE,
    _PREDICTIVE_RE,
    _PRESCRIPTIVE_RE,
    _COMPARISON_RE,
    _LOOKUP_RE,
    _HEALTH_RE,
)


def has_analytical_signal(query: str, metrics: MetricRegistry | None = None) -> bool:
    """True when the query carries a recognizable metric or analysis intent.

    ``classify_intents`` falls back to ``diagnostic`` for anything it does not
    recognize, so it cannot tell a real question ("why did CAC rise?") from
    noise ("a", "?????", a pasted SQL string). This checks for an *explicit*
    hook: an intent verb (why / forecast / compare / recommend / health), a
    known metric alias, or a resolvable primary metric. Missions with no such
    signal are routed to an ``ROUTING_UNSUPPORTED`` result instead of
    fabricating a synthetic diagnosis against fixture defaults.
    """
    text = query or ""
    if any(rx.search(text) for rx in _ANALYTICAL_RES):
        return True
    lowered = text.lower()
    if any(alias in lowered for alias in _METRIC_ALIASES):
        return True
    # Cheap admission check, no live MCP call here — the offline resolver is
    # sync and sufficient (the authoritative dynamic resolution happens later
    # in normalize_query, once we know execution_mode).
    primary, _secondary, _reason = _resolve_metrics_offline(text)
    return primary is not None


def classify_intents(query: str) -> list[str]:
    intents: list[str] = []
    if _HEALTH_RE.search(query):
        intents.append("executive_health")
    if _DIAGNOSTIC_RE.search(query):
        intents.append("diagnostic")
    if _PREDICTIVE_RE.search(query):
        intents.append("predictive")
    if _PRESCRIPTIVE_RE.search(query):
        intents.append("prescriptive")
    if _COMPARISON_RE.search(query):
        intents.append("comparison")
    if _LOOKUP_RE.search(query) and not intents:
        intents.append("lookup")
    # Executive health is a diagnostic investigation (why are we unhealthy), not
    # prediction/strategy alone — otherwise specialists skip Diagnostic/Skeptic.
    if "executive_health" in intents and "diagnostic" not in intents:
        intents.append("diagnostic")
    # Unrecognized phrasing defaults to the cheapest tier (observer-only lookup),
    # not the full investigation pipeline — this also drives route_for's
    # swarm-vs-lookup decision, so a plain "get today's X" stays on the fast path.
    if not intents:
        intents.append("lookup")
    return intents


def apply_full_flags(
    intents: set[str] | list[str],
    *,
    full_diagnostic: bool = False,
    full_prediction: bool = False,
    full_skeptic: bool = False,
) -> set[str]:
    """Ensure full_* request flags activate the matching specialist intents.

    ``full_*`` historically only swapped in the full agent bridge implementation.
    Callers (especially the HTTP API, where these default to True) also expect the
    specialist to *run*. Without this, ``full_prediction=True`` on a pure "why"
    query registers PredictionAgent but never activates it.
    """
    out = set(intents)
    if full_diagnostic:
        out.add("diagnostic")
    if full_prediction:
        out.add("predictive")
    if full_skeptic and not (out & {"diagnostic", "predictive", "prescriptive", "executive_health"}):
        # Skeptic needs a claim-bearing path; diagnostic is the minimum.
        out.add("diagnostic")
    return out


_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "if", "then", "than", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "this", "that", "these", "those", "it",
    "we", "us", "our", "do", "does", "did", "has", "have", "had", "not",
    "why", "what", "when", "where", "how", "which", "who", "should",
    "over", "last", "past", "next", "days", "day", "week", "weeks",
    "month", "months", "year", "years", "today", "yesterday", "continues",
    "happen", "happens", "action", "get", "me", "show", "tell", "please",
}


def _candidate_terms(query: str, *, max_terms: int = 12) -> list[str]:
    """Generic n-gram phrase extraction — no metric-specific vocabulary.

    Longer windows first (more likely to hit a specific glossary phrase like
    "net sales" before wasting a call on a lone generic word).
    """
    words = [w for w in re.findall(r"[a-zA-Z]+", query.lower()) if w not in _STOPWORDS]
    candidates: list[str] = []
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i : i + size])
            if phrase and phrase not in candidates:
                candidates.append(phrase)
    return candidates[:max_terms]


async def _resolve_metrics_via_catalogue(
    query: str,
    *,
    mcp: Any,
    agent_id: str,
    metrics: MetricRegistry | None,
) -> tuple[str | None, list[str], str | None]:
    """Resolve query language to metric ids via the live Seleric MCP catalogue.

    No local alias table: every candidate phrase is checked against the
    catalogue's own glossary (catalogue_resolve_term), which is the single
    source of truth for what a business term means.
    """
    found: list[str] = []
    reasons: list[str] = []
    seen: set[str] = set()
    for term in _candidate_terms(query):
        try:
            result = await mcp.call(
                agent_id=agent_id, capability="seleric.catalogue_resolve_term", arguments={"text": term}
            )
        except Exception:
            continue
        if result.get("kind") != "resolved":
            continue
        catalogue_id = result.get("metric_id")
        if not catalogue_id or catalogue_id in seen:
            continue
        seen.add(catalogue_id)
        registry_id = metrics.id_for_catalogue(catalogue_id) if metrics else None
        resolved = registry_id or f"metric.{catalogue_id}"
        found.append(resolved)
        reasons.append(f"{term}->{resolved} (catalogue, confidence={result.get('confidence')})")
        if len(found) >= 3:
            break
    primary = found[0] if found else None
    return primary, found[1:], "; ".join(reasons) or None


def _resolve_metrics_offline(query: str) -> tuple[str | None, list[str], str | None]:
    """Fixture-mode fallback only — no live MCP available. Kept deterministic
    on purpose so offline/synthetic missions stay reproducible in tests.
    """
    q = query.lower()
    found: list[str] = []
    reason_parts: list[str] = []
    for alias, metric_id in _METRIC_ALIASES.items():
        if alias in q:
            found.append(metric_id)
            reason_parts.append(f"{alias}->{metric_id}")
    found = list(dict.fromkeys(found))
    primary = found[0] if found else None
    reason = "; ".join(reason_parts) if reason_parts else None
    if "cac" in q and primary is None:
        primary = "metric.cac"
        reason = "default CAC alias -> metric.cac"
    return primary, found[1:], reason


async def resolve_metrics(
    query: str,
    metrics: MetricRegistry | None = None,
    *,
    mcp: Any | None = None,
    agent_id: str = "coordinator_agent",
) -> tuple[str | None, list[str], str | None]:
    """Resolve the metric(s) a query is about.

    Live mode (``mcp`` given, with the catalogue capability registered):
    resolves dynamically against the Seleric MCP catalogue — no hardcoded
    metric vocabulary. Falls back to the static offline alias table only
    when no live catalogue is available (fixture/offline missions).
    """
    if mcp is not None and "seleric.catalogue_resolve_term" in getattr(mcp, "capabilities", set()):
        primary, secondary, reason = await _resolve_metrics_via_catalogue(
            query, mcp=mcp, agent_id=agent_id, metrics=metrics
        )
        if primary is not None:
            return primary, secondary, reason
    return _resolve_metrics_offline(query)


def resolve_entities(query: str) -> list[EntityRef]:
    entities: list[EntityRef] = []
    for pattern, etype in _ENTITY_PATTERNS:
        m = re.search(pattern, query, re.IGNORECASE)
        if m:
            raw = m.group(0)
            entities.append(
                EntityRef(
                    entity_type=etype,
                    entity_id=raw.lower(),
                    raw=raw,
                    resolved=True,
                    resolution_reason="pattern_match",
                )
            )
    return entities


def _as_of_date(as_of: str | None, tz: ZoneInfo) -> date:
    if isinstance(as_of, str) and as_of.strip():
        try:
            return date.fromisoformat(as_of[:10])
        except ValueError as exc:
            raise ValueError(f"as_of is not a valid date: {as_of!r}") from exc
    return datetime.now(tz).date()


def resolve_time_range(
    query: str,
    *,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
) -> tuple[TimeRange | None, TimeRange | None]:
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    today = _as_of_date(as_of, tz)
    q = query.lower()

    def day_range(d: date, label: str) -> TimeRange:
        return TimeRange(
            start=d.isoformat(),
            end=d.isoformat(),
            timezone=timezone,
            label=label,
        )

    if "yesterday" in q:
        d = today - timedelta(days=1)
        return day_range(d, "yesterday"), None
    if "last three days" in q or "past three days" in q or "last 3 days" in q:
        start = today - timedelta(days=3)
        return TimeRange(
            start=start.isoformat(), end=today.isoformat(), timezone=timezone, label="last_three_days"
        ), None
    if "today" in q:
        return day_range(today, "today"), None
    if "this week" in q:
        start = today - timedelta(days=today.weekday())
        return TimeRange(
            start=start.isoformat(), end=today.isoformat(), timezone=timezone, label="this_week"
        ), None
    if "last month" in q:
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return TimeRange(
            start=last_month_start.isoformat(),
            end=last_month_end.isoformat(),
            timezone=timezone,
            label="last_month",
        ), None
    # default: last 3 days for diagnostic-style questions
    start = today - timedelta(days=3)
    return TimeRange(
        start=start.isoformat(), end=today.isoformat(), timezone=timezone, label="recent_window"
    ), None


def candidate_domains(query: str, intents: list[str], primary_metric: str | None) -> list[str]:
    domains: list[str] = []
    q = query.lower()
    if primary_metric and "cac" in (primary_metric or "") or "cac" in q:
        domains.extend(["performance", "funnel", "technical"])
    if any(k in q for k in ("sales", "orders", "shopify", "revenue")):
        domains.append("commerce")
    if any(k in q for k in ("funnel", "cvr", "checkout", "sessions")):
        domains.append("funnel")
    if any(k in q for k in ("latency", "lcp", "js error", "deploy", "mobile")):
        domains.append("technical")
    if "executive_health" in intents:
        domains.extend(["commerce", "performance", "funnel", "finance"])
    if not domains:
        domains.append("performance")
    return list(dict.fromkeys(domains))


async def normalize_query(
    query: str,
    *,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    metrics: MetricRegistry | None = None,
    requested_outputs: list[str] | None = None,
    mcp: Any | None = None,
    agent_id: str = "coordinator_agent",
    runtime: Any | None = None,
    mission_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
) -> NormalizedQuery:
    """Classify intents/metrics/entities/domains.

    When ``runtime`` is given (live missions), classification goes through
    the LLM + live catalogue (coordinator.intake.llm_classifier) — no keyword
    lists. Falls back to the offline regex classifier only when no runtime is
    given (fixture-free unit tests, or the LLM call itself failed).
    """
    llm_result = None
    if runtime is not None:
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
        )

    if llm_result is not None:
        intents = list(llm_result.intents)
        primary = llm_result.primary_metric
        secondary = llm_result.secondary_metrics
        reason = f"llm+catalogue: {primary}" if primary else None
        entities = [
            EntityRef(entity_type="dimension", entity_id=e, raw=e, resolved=True, resolution_reason="llm_catalogue")
            for e in llm_result.entities
        ]
        tr = llm_result.time_range
        time_range = (
            TimeRange(start=tr.start, end=tr.end or tr.start, timezone=timezone, label=tr.relative_token)
            if tr.start
            else None
        )
        comparison = None
        domains = (
            [llm_result.domain_lead.removesuffix("_agent")]
            if llm_result.domain_lead
            else candidate_domains(query, intents, primary)
        )
    else:
        intents = classify_intents(query)
        primary, secondary, reason = await resolve_metrics(query, metrics, mcp=mcp, agent_id=agent_id)
        entities = resolve_entities(query)
        time_range, comparison = resolve_time_range(query, timezone=timezone, as_of=as_of)
        domains = candidate_domains(query, intents, primary)
    unresolved: list[str] = []
    if primary is None and "lookup" in intents:
        unresolved.append("primary_metric_unresolved")
    # Causal / forecast questions without a resolvable metric still run, but
    # surface the gap so synthesis/limitations can explain fixture fallback.
    if primary is None and any(i in intents for i in ("diagnostic", "predictive", "prescriptive")):
        unresolved.append("primary_metric_unresolved")
    return NormalizedQuery(
        original_query=query,
        intents=intents,
        primary_metric=primary,
        secondary_metrics=secondary,
        entities=entities,
        time_range=time_range,
        comparison_range=comparison,
        requested_outputs=list(requested_outputs or []),
        candidate_domains=domains,
        unresolved_semantics=unresolved,
        metric_resolution_reason=reason,
    )


def resolve_mission_time_range(
    scenario: dict,
    *,
    timezone: str,
    as_of: str | None = None,
    normalized: NormalizedQuery | None = None,
) -> dict[str, str | None]:
    """Build the observation window used for MCP/fixture fetches.

    Preference order:
    1. Scenario ``observation_window`` (preserves fixture degradation arcs)
    2. Query-derived ``normalized.time_range`` when no scenario window
    3. ``as_of`` alone

    When ``as_of`` is past the window end, extend ``end`` to ``as_of`` so MCP
    fetches include the client observation day (without rewriting the start).
    """
    window = dict(scenario.get("observation_window") or {})
    as_of_day: str | None = None
    if as_of:
        try:
            as_of_day = date.fromisoformat(str(as_of)[:10]).isoformat()
        except ValueError:
            as_of_day = None

    start = str(window["start"])[:10] if window.get("start") else None
    end = str(window["end"])[:10] if window.get("end") else None
    # Preserve the fixture/scenario arc end for single-day MCP fetches even if
    # client as_of extends the reported observation window.
    observation_end = end

    if (start is None or end is None) and normalized is not None and normalized.time_range is not None:
        tr = normalized.time_range
        n_start = getattr(tr, "start", None)
        n_end = getattr(tr, "end", None) or n_start
        if n_start and n_end:
            start = start or str(n_start)[:10]
            end = end or str(n_end)[:10]
            observation_end = observation_end or end

    if as_of_day:
        end = max(end or as_of_day, as_of_day)
        if not start or start > end:
            start = as_of_day if not start else min(start, as_of_day)

    if not start and not end:
        start = end = as_of_day
        observation_end = as_of_day

    out: dict[str, str | None] = {"start": start, "end": end, "timezone": timezone}
    if observation_end:
        out["observation_end"] = observation_end
    return out


def complexity_band(normalized: NormalizedQuery) -> str:
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

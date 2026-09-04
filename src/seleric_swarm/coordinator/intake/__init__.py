"""Query intake — normalize intents, metrics, entities, and time ranges."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
    "cac": "metric.blended_paid_cac",
    "blended cac": "metric.blended_paid_cac",
    "paid cac": "metric.blended_paid_cac",
    "cpm": "metric.cpm",
    "ctr": "metric.ctr",
    "cpc": "metric.cpc",
    "roas": "metric.roas",
    "net sales": "metric.net_sales",
    "sales": "metric.net_sales",
    "shopify sales": "metric.net_sales",
    "orders": "metric.orders",
    "purchase cvr": "metric.purchase_cvr",
    "conversion": "metric.purchase_cvr",
    "cvr": "metric.purchase_cvr",
}

_ENTITY_PATTERNS: list[tuple[str, str]] = [
    (r"\bmobile\b", "device"),
    (r"\bdesktop\b", "device"),
    (r"\bmeta\b|\bfacebook\b", "channel"),
    (r"\bgoogle\b", "channel"),
    (r"\bcheckout\b", "funnel_stage"),
    (r"\bpurchase\b", "funnel_stage"),
]


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
    if not intents:
        intents.append("diagnostic")
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


def resolve_metrics(query: str, metrics: MetricRegistry | None = None) -> tuple[str | None, list[str], str | None]:
    q = query.lower()
    found: list[str] = []
    reason_parts: list[str] = []
    for alias, metric_id in _METRIC_ALIASES.items():
        if alias in q:
            if metrics is not None and metrics.get(metric_id) is None:
                # still accept known alias ids used by swarm fixtures
                pass
            found.append(metric_id)
            reason_parts.append(f"{alias}->{metric_id}")
    # preserve order, unique
    found = list(dict.fromkeys(found))
    primary = found[0] if found else None
    secondary = found[1:]
    reason = "; ".join(reason_parts) if reason_parts else None
    if "cac" in q and primary is None:
        primary = "metric.blended_paid_cac"
        reason = "default CAC alias -> metric.blended_paid_cac"
    return primary, secondary, reason


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
    if as_of:
        try:
            return date.fromisoformat(as_of[:10])
        except ValueError:
            pass
    return datetime.now(tz).date()


def resolve_time_range(
    query: str,
    *,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
) -> tuple[TimeRange | None, TimeRange | None]:
    tz = ZoneInfo(timezone)
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


def normalize_query(
    query: str,
    *,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    metrics: MetricRegistry | None = None,
    requested_outputs: list[str] | None = None,
) -> NormalizedQuery:
    intents = classify_intents(query)
    primary, secondary, reason = resolve_metrics(query, metrics)
    entities = resolve_entities(query)
    time_range, comparison = resolve_time_range(query, timezone=timezone, as_of=as_of)
    domains = candidate_domains(query, intents, primary)
    unresolved: list[str] = []
    if primary is None and "lookup" in intents:
        unresolved.append("primary_metric_unresolved")
    if primary is None and any(i in intents for i in ("diagnostic", "predictive", "prescriptive")):
        # Causal / forecast questions without a resolvable metric still run, but
        # surface the gap so synthesis/limitations can explain fixture fallback.
        if not any(alias in query.lower() for alias in _METRIC_ALIASES):
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

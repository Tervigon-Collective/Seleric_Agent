from __future__ import annotations

import json
import re
import time
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from seleric_swarm.llm.errors import FallbackDisabled
from seleric_swarm.llm.port import LLMRequest, LLMResponse, StructuredLLMResponse, TokenUsage
from seleric_swarm.llm.structured import parse_structured, with_schema_instruction
from seleric_swarm.services.metrics import MetricRegistry, lead_agent_for_hints

_REGISTRY: MetricRegistry | None = None


def _registry() -> MetricRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = MetricRegistry("config/metric_registry.yaml")
    return _REGISTRY


def _words(text: str) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", (text or "").lower())
    out: set[str] = set()
    for word in raw:
        out.add(word)
        if word.endswith("s") and len(word) > 3:
            out.add(word[:-1])
        if word in {"sale", "sales"}:
            out.update({"sale", "sales"})
    return out


def _mention_index(query: str, phrases: list[str]) -> int:
    best: int | None = None
    for phrase in phrases:
        if not phrase:
            continue
        found = query.find(phrase)
        if found >= 0:
            best = found if best is None else min(best, found)
    return 10_000 if best is None else best


def hints_from_registry(query: str, metrics: MetricRegistry | None = None) -> list[str]:
    """Test-double metric match against the live registry — not a question table."""
    metrics = metrics or _registry()
    q = (query or "").lower()
    q_words = _words(q)
    wants_gross = "gross" in q_words
    mentions_net = "net" in q_words
    attributed = "attributed" in q_words
    by_domain: dict[str, list] = {}
    for metric in metrics.all():
        by_domain.setdefault(metric.domain, []).append(metric)

    scored: list[tuple[int, int, str]] = []
    for metric in metrics.all():
        slug = metric.id.removeprefix("metric.")
        slug_phrase = slug.replace("_", " ")
        slug_parts = [p for p in slug.split("_") if p]
        phrases = [slug_phrase, slug_phrase.replace("sales", "sale"), *metric.aliases]
        score = 0
        if slug_phrase and slug_phrase in q or "sales" in slug_phrase and slug_phrase.replace("sales", "sale") in q or slug_parts and all(part in q_words for part in slug_parts):
            score = 10
        else:
            for alias in metric.aliases:
                if alias not in q:
                    continue
                if metric.id == "metric.net_sales" and wants_gross and not mentions_net:
                    continue
                if (
                    metric.id == "metric.net_sales"
                    and attributed
                    and "sale" not in q_words
                    and "sales" not in q_words
                ):
                    continue
                score = 8
                break
        if score == 0 and metric.domain in q_words and len(by_domain.get(metric.domain) or []) == 1:
            score = 7
            phrases = [metric.domain, *phrases]
        if score == 0 and "channel" in q_words and re.search(r"\bacross channels\b", metric.description or "", re.IGNORECASE):
            score = 8
            phrases = ["channel", *phrases]
        if score == 0:
            continue
        scored.append((score, _mention_index(q, phrases), metric.id))

    scored.sort(key=lambda item: item[1])
    out: list[str] = []
    for _score, _index, metric_id in scored:
        if metric_id not in out:
            out.append(metric_id)
    return out

_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_SALES_WORD = re.compile(r"\bsales\b")


def _user_text(request: LLMRequest) -> str:
    parts = [m.content for m in request.messages if m.role == "user"]
    return "\n".join(parts)


def _extract_query(text: str) -> str:
    for line in text.splitlines():
        if line.lower().startswith("query:"):
            return line.split(":", 1)[1].strip()
    return text.strip()


def _extract_field(text: str, label: str) -> str | None:
    prefix = f"{label.lower()}:"
    for line in text.splitlines():
        if line.lower().startswith(prefix):
            value = line.split(":", 1)[1].strip()
            if value and value.lower() not in {"none", "null", ""}:
                return value
    return None


def resolve_as_of(as_of: str | None, timezone: str) -> date:
    if as_of:
        return date.fromisoformat(as_of[:10])
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except ZoneInfoNotFoundError:
        return datetime.now(UTC).date()


def _time_range_for(q: str, lower: str) -> tuple[dict[str, Any], str]:
    """Return (time_range, query_class) for a single-metric lookup/comparison."""
    dates = _DATE_RE.findall(q)
    if "compare" in lower or " versus " in lower or " vs " in lower or len(dates) >= 2:
        if len(dates) >= 2:
            return (
                {"kind": "comparison", "start": dates[0], "end": dates[1], "relative_token": None},
                "comparison",
            )
        return (
            {
                "kind": "comparison",
                "start": None,
                "end": None,
                "relative_token": "yesterday_vs_as_of" if "yesterday" in lower else None,
            },
            "comparison",
        )
    if dates:
        return {"kind": "absolute", "start": dates[0], "end": dates[0], "relative_token": None}, "lookup"
    last_n = re.search(r"\blast\s+(\d+)\s+days?\b", lower)
    if last_n:
        return (
            {"kind": "relative", "start": None, "end": None, "relative_token": f"last_{last_n.group(1)}d"},
            "lookup",
        )
    if "yesterday" in lower:
        return {"kind": "relative", "start": None, "end": None, "relative_token": "yesterday"}, "lookup"
    if "today" in lower:
        return {"kind": "relative", "start": None, "end": None, "relative_token": "today"}, "lookup"
    return {"kind": "relative", "start": None, "end": None, "relative_token": "yesterday"}, "lookup"


def _unsupported(domain_lead: str, reason: str) -> dict[str, Any]:
    return {
        "query_class": "unsupported",
        "domain_lead": domain_lead,
        "entities": [],
        "time_range": {"kind": "none"},
        "metric_hints": [],
        "unsupported_reason": reason,
    }


def _collect_hints(lower: str) -> list[str]:
    return hints_from_registry(lower)


def _lead_for_hints(hints: list[str]) -> str:
    return lead_agent_for_hints(hints, _registry())


def classify_lookup_query(query: str, timezone: str, as_of: str | None) -> dict[str, Any]:
    q = query.strip()
    lower = q.lower()

    has_performance = any(k in lower for k in ("cac", "roas", "cpm", "cpc", "ctr", "impressions", "ad spend"))

    diagnostic_markers = (
        "why ",
        "why did",
        "root cause",
        "what should we",
        "recommend",
        "email ",
        "vendor",
        "hack",
        "ignore previous",
        "ignore policy",
        "system prompt",
    )
    if any(marker in lower for marker in diagnostic_markers):
        domain = "performance_agent" if has_performance else "coordinator_agent"
        return {
            "query_class": "unsupported",
            "domain_lead": domain,
            "entities": [],
            "time_range": {"kind": "none"},
            "metric_hints": [],
            "unsupported_reason": "V1 only supports commerce lookup/comparison questions",
        }

    hints = _collect_hints(lower)
    if hints:
        time_range, query_class = _time_range_for(q, lower)
        return {
            "query_class": query_class,
            "domain_lead": _lead_for_hints(hints),
            "entities": [],
            "time_range": time_range,
            "metric_hints": hints,
            "unsupported_reason": None,
        }

    # Unregistered glossary terms still get a domain so gold/eval can assert lead.
    if "roas" in lower:
        return _unsupported("performance_agent", "No registered ROAS metric could be inferred")

    return {
        "query_class": "unsupported",
        "domain_lead": "coordinator_agent",
        "entities": [],
        "time_range": {"kind": "none"},
        "metric_hints": [],
        "unsupported_reason": "No registered commerce metric could be inferred",
    }


def map_metric(
    query: str,
    hints: list[str],
    allowed: list[str] | None = None,
) -> dict[str, Any]:
    lower = query.lower()
    candidates: list[str] = []
    if "cac" in lower:
        candidates.append("metric.cac")
    if "gross sale" in lower:
        candidates.append("metric.gross_sales")
    if "attributed" in lower and "revenue" in lower:
        candidates.append("metric.attributed_net_revenue")
    if "net sale" in lower or (
        "gross sale" not in lower
        and ("revenue" in lower or _SALES_WORD.search(lower) is not None)
        and "attributed" not in lower
    ):
        candidates.append("metric.net_sales")
    elif "net profit" in lower or "profit" in lower:
        candidates.append("metric.net_profit")
    elif "atc rate" in lower or "add-to-cart rate" in lower or "add to cart rate" in lower:
        candidates.append("metric.atc_rate")
    elif "units sold" in lower:
        candidates.append("metric.units_sold")
    elif "repeat rate" in lower:
        candidates.append("metric.repeat_rate")
    elif "refunded amount" in lower or "refund amount" in lower:
        candidates.append("metric.refunded_amount_excl_tax")
    allowed_set = set(allowed or [])
    if allowed_set:
        candidates = [item for item in candidates if item in allowed_set]
        hint_ok = [h for h in hints if h in allowed_set]
    else:
        hint_ok = hints
    if len(candidates) == 1:
        return {"metric_id": candidates[0], "ambiguous": False, "reason": None}
    if len(candidates) > 1:
        for hint in hint_ok:
            if hint in candidates:
                return {"metric_id": hint, "ambiguous": False, "reason": None}
        return {"metric_id": candidates[0], "ambiguous": False, "reason": None}
    if hint_ok:
        return {"metric_id": hint_ok[0], "ambiguous": False, "reason": None}
    return {
        "metric_id": None,
        "ambiguous": True,
        "reason": "Could not map the question to a registered metric id",
    }


def _claims_from_user(text: str) -> list[dict[str, Any]]:
    marker = "GATED_CLAIMS_JSON:"
    # The template renders this on its own line, followed by EVIDENCE_JSON on
    # the next line — take only that line's payload so trailing template
    # content doesn't corrupt json.loads (a strict parser rejects any string
    # with trailing data, so "[...]\nEVIDENCE_JSON: ..." would silently parse
    # to zero claims and mask a passed, gated claim as "no claims available").
    line = next((ln for ln in text.splitlines() if ln.strip().startswith(marker)), None)
    if line is None:
        return []
    raw = line.split(marker, 1)[1].strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return payload
    return []


def synthesize_response(text: str) -> str:
    claims = _claims_from_user(text)
    if not claims:
        return "No validated claims are available."
    lines = []
    for claim in claims:
        claim_text = str(claim.get("text", "")).strip()
        refs = ", ".join(claim.get("support_refs") or [])
        if refs:
            lines.append(f"{claim_text} Evidence: {refs}.")
        else:
            lines.append(claim_text)
    return " ".join(lines)


class FakeLLMAdapter:
    """Deterministic adapter for tests and CI. Does not call the network."""

    def __init__(self, model: str = "fake-llama") -> None:
        self.model = model

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if request.fallback_model:
            raise FallbackDisabled()
        started = time.perf_counter()
        text = self._render(request)
        latency_ms = (time.perf_counter() - started) * 1000
        return LLMResponse(
            text=text,
            model=request.model or self.model,
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=12, completion_tokens=24, total_tokens=36),
            latency_ms=latency_ms,
            retry_count=0,
            provider_request_id="fake-req",
        )

    async def complete_structured(
        self, request: LLMRequest, schema: type[BaseModel]
    ) -> StructuredLLMResponse:
        prepared = with_schema_instruction(request, schema)
        raw = await self.complete(prepared)
        value = parse_structured(raw, schema)
        return StructuredLLMResponse(value=value, raw=raw)

    def _render(self, request: LLMRequest) -> str:
        prompt_id = (request.metadata.prompt_id or "").lower()
        user = _user_text(request)
        joined = "\n".join(m.content for m in request.messages).lower()

        if "ping" in joined and "query:" not in joined:
            return "pong"

        query = _extract_query(user)
        timezone = _extract_field(user, "Timezone") or "Asia/Kolkata"
        as_of = _extract_field(user, "As-of")

        if prompt_id.endswith("classify") or "coordinator.classify" in prompt_id:
            return json.dumps(classify_lookup_query(query, timezone, as_of))
        if prompt_id.endswith("metric_map") or "observer.metric_map" in prompt_id:
            hints_field = _extract_field(user, "Metric hints") or ""
            hints = [h.strip() for h in hints_field.split(",") if h.strip()]
            allowed_field = _extract_field(user, "Allowed metric ids") or ""
            allowed = [item.strip() for item in allowed_field.split(",") if item.strip()]
            return json.dumps(map_metric(query, hints, allowed or None))
        if prompt_id.endswith("response") or "synthesizer" in prompt_id:
            return synthesize_response(user)
        if "json schema" in joined or request.response_format == "json_schema":
            return json.dumps(classify_lookup_query(query, timezone, as_of))
        return "pong"

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

_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


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


def classify_lookup_query(query: str, timezone: str, as_of: str | None) -> dict[str, Any]:
    q = query.strip()
    lower = q.lower()

    has_commerce = any(k in lower for k in ("net sales", "gross sales", "revenue")) or (
        "sales" in lower and "ad spend" not in lower
    )
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

    if has_commerce and has_performance:
        metric_hints: list[str] = []
        if "cac" in lower:
            metric_hints.append("metric.cac")
        if "gross sales" in lower:
            metric_hints.append("metric.gross_sales")
        elif "net sales" in lower or "revenue" in lower or "sales" in lower:
            metric_hints.append("metric.net_sales")
        dates = _DATE_RE.findall(q)
        if dates:
            time_range: dict[str, Any] = {
                "kind": "absolute",
                "start": dates[0],
                "end": dates[0],
                "relative_token": None,
            }
        elif "yesterday" in lower:
            time_range = {"kind": "relative", "start": None, "end": None, "relative_token": "yesterday"}
        else:
            time_range = {"kind": "relative", "start": None, "end": None, "relative_token": "yesterday"}
        return {
            "query_class": "lookup",
            "domain_lead": "performance_agent",
            "entities": [],
            "time_range": time_range,
            "metric_hints": metric_hints,
            "unsupported_reason": None,
        }

    performance_metrics = ("cac", "roas", "cpm", "cpc", "ctr", "impressions", "ad spend", "spend")
    if any(k in lower for k in performance_metrics):
        return {
            "query_class": "unsupported",
            "domain_lead": "performance_agent",
            "entities": [],
            "time_range": {"kind": "none"},
            "metric_hints": [],
            "unsupported_reason": "Performance domain is not activated in V1",
        }

    funnel_metrics = ("sessions", "add to cart", "atc", "checkout", "funnel")
    if any(k in lower for k in funnel_metrics) and "sales" not in lower:
        return {
            "query_class": "unsupported",
            "domain_lead": "funnel_agent",
            "entities": [],
            "time_range": {"kind": "none"},
            "metric_hints": [],
            "unsupported_reason": "Funnel domain is not activated in V1",
        }

    metric_hints = []
    if "gross sales" in lower:
        metric_hints.append("metric.gross_sales")
    elif "net sales" in lower or "revenue" in lower or "sales" in lower:
        metric_hints.append("metric.net_sales")

    if not metric_hints:
        return {
            "query_class": "unsupported",
            "domain_lead": "coordinator_agent",
            "entities": [],
            "time_range": {"kind": "none"},
            "metric_hints": [],
            "unsupported_reason": "No registered commerce metric could be inferred",
        }

    dates = _DATE_RE.findall(q)
    kind = "lookup"
    if "compare" in lower or " versus " in lower or " vs " in lower or len(dates) >= 2:
        kind = "comparison"

    if kind == "comparison":
        if len(dates) >= 2:
            time_range = {
                "kind": "comparison",
                "start": dates[0],
                "end": dates[1],
                "relative_token": None,
            }
        else:
            time_range = {
                "kind": "comparison",
                "start": None,
                "end": None,
                "relative_token": "yesterday_vs_as_of" if "yesterday" in lower else None,
            }
        return {
            "query_class": "comparison",
            "domain_lead": "commerce_agent",
            "entities": [],
            "time_range": time_range,
            "metric_hints": metric_hints,
            "unsupported_reason": None,
        }

    if dates:
        time_range = {"kind": "absolute", "start": dates[0], "end": dates[0], "relative_token": None}
    elif "yesterday" in lower:
        time_range = {"kind": "relative", "start": None, "end": None, "relative_token": "yesterday"}
    elif "today" in lower:
        time_range = {"kind": "relative", "start": None, "end": None, "relative_token": "today"}
    else:
        time_range = {"kind": "relative", "start": None, "end": None, "relative_token": "yesterday"}

    return {
        "query_class": "lookup",
        "domain_lead": "commerce_agent",
        "entities": [],
        "time_range": time_range,
        "metric_hints": metric_hints,
        "unsupported_reason": None,
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
    if "gross sales" in lower:
        candidates.append("metric.gross_sales")
    elif "net sales" in lower or "revenue" in lower or "sales" in lower:
        candidates.append("metric.net_sales")
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
    if marker not in text:
        return []
    raw = text.split(marker, 1)[1].strip()
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

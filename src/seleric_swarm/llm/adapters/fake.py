from __future__ import annotations

import json
import re
import time
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

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


# ponytail: word-level typo tolerance via ratio, not a real spellchecker —
# upgrade to a proper fuzzy-match lib if aliases start needing more slack.
def _fuzzy_word_in(word: str, q_words: set[str]) -> bool:
    if word in q_words:
        return True
    return any(len(w) >= 4 and SequenceMatcher(None, word, w).ratio() >= 0.8 for w in q_words)


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

    # Special pattern: "top"/"best" + "product" = units_sold
    # Handle both correct spelling and common typos (seeling for selling)
    top_best_product_pattern = re.compile(r"\b(top|best).*\b(product|seeling|selling)")
    if top_best_product_pattern.search(q):
        units_metric = metrics.get("metric.units_sold")
        if units_metric:
            return ["metric.units_sold"]

    scored: list[tuple[int, int, str]] = []
    weak_channel_desc_matches: list[tuple[int, int, str]] = []
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
                alias_parts = [p for p in alias.split() if p]
                short_alias = len(alias_parts) == 1 and len(alias_parts[0]) <= 3
                if short_alias:
                    if alias_parts[0] not in q_words:
                        continue
                elif alias not in q and not all(_fuzzy_word_in(p, q_words) for p in alias_parts):
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
        if (
            score == 0
            and metric.domain in q_words
            and len(by_domain.get(metric.domain) or []) > 1
            and _AREA_STATUS_RE.search(q)
        ):
            # "funnel status" names the whole area, not one measure — bundle
            # every metric in that domain instead of dropping to zero hints.
            score = 6
            phrases = [metric.domain, *phrases]
        if score == 0 and "channel" in q_words and re.search(r"\bacross channels\b", metric.description or "", re.IGNORECASE):
            # Weakest signal here: any query merely containing the word
            # "channel" matches every metric whose *description* happens to
            # say "across channels", regardless of whether that metric is
            # actually what the query named (e.g. "net profit by channel"
            # dragging in attributed_net_revenue/orders/gross_revenue purely
            # because their descriptions all end in "across channels").
            # Tracked separately so a real slug/alias match elsewhere in the
            # same query can suppress just this weak tier, not every hint.
            weak_channel_desc_matches.append((8, _mention_index(q, ["channel", *phrases]), metric.id))
            continue
        if score == 0:
            continue
        scored.append((score, _mention_index(q, phrases), metric.id))

    if not scored:
        scored = weak_channel_desc_matches
    scored.sort(key=lambda item: item[1])
    out: list[str] = []
    for _score, _index, metric_id in scored:
        if metric_id not in out:
            out.append(metric_id)
    return out

_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_SALES_WORD = re.compile(r"\bsales\b")
_AREA_STATUS_RE = re.compile(r"\b(status|health|doing|performing|performance|overview)\b")


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
    last_n_weeks = re.search(r"\blast\s+(\d+)\s+weeks?\b", lower)
    if last_n_weeks:
        return (
            {"kind": "relative", "start": None, "end": None, "relative_token": f"last_{last_n_weeks.group(1)}w"},
            "lookup",
        )
    last_n_months = re.search(r"\blast\s+(\d+)\s+months?\b", lower)
    if last_n_months:
        return (
            {"kind": "relative", "start": None, "end": None, "relative_token": f"last_{last_n_months.group(1)}m"},
            "lookup",
        )
    last_n_quarters = re.search(r"\blast\s+(\d+)\s+quarters?\b", lower)
    if last_n_quarters:
        return (
            {"kind": "relative", "start": None, "end": None, "relative_token": f"last_{last_n_quarters.group(1)}q"},
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
    # Performance metrics (CAC, ROAS, etc.) take priority when mixed with other domains
    performance_priority = ["metric.cac", "metric.roas", "metric.gross_roas", "metric.net_roas"]
    for metric_id in performance_priority:
        if metric_id in hints:
            return "performance_agent"
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
        domain_lead = "performance_agent" if has_performance else _lead_for_hints(hints)
        return {
            "query_class": query_class,
            "domain_lead": domain_lead,
            "entities": [],
            "time_range": time_range,
            "metric_hints": hints,
            "unsupported_reason": None,
        }

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
        return {"metric_ids": candidates, "ambiguous": False, "reason": None}
    if len(candidates) > 1:
        for hint in hint_ok:
            if hint in candidates:
                return {"metric_ids": [hint], "ambiguous": False, "reason": None}
        return {"metric_ids": [candidates[0]], "ambiguous": False, "reason": None}
    if hint_ok:
        return {"metric_ids": [hint_ok[0]], "ambiguous": False, "reason": None}
    if allowed_set and _AREA_STATUS_RE.search(lower):
        # "funnel status" / "how's checkout doing" names an area, not one
        # measure — bundle every allowed metric rather than failing closed.
        return {"metric_ids": sorted(allowed_set), "ambiguous": False, "reason": None}
    return {
        "metric_ids": [],
        "ambiguous": True,
        "reason": "Could not map the question to a registered metric id",
    }


def map_dimension(query: str, supported: list[str]) -> dict[str, Any]:
    """Stand-in for observer.dimension_map: pick the supported dimension whose
    name is actually mentioned in the query, so tests exercise the same
    grounded-in-real-data contract the real LLM prompt does."""
    lower = query.lower()
    hits: list[str] = []
    for dim in supported:
        words = [w for w in dim.removeprefix("lt_").split("_") if len(w) >= 4]
        if any(re.search(rf"\b{re.escape(w)}s?\b", lower) for w in words):
            hits.append(dim)
    return {"dimensions": hits[:1]}


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
        if prompt_id.endswith("dimension_map") or "observer.dimension_map" in prompt_id:
            supported_field = _extract_field(user, "Supported dimensions for this metric") or ""
            supported = [item.strip() for item in supported_field.split(",") if item.strip()]
            return json.dumps(map_dimension(query, supported))
        if prompt_id == "synthesizer.swarm_response":
            # No scripted business-prose generator for swarm_v2 synthesis —
            # returning empty defers to the deterministic template fallback
            # (coordinator/synthesis/response_builder.py) that tests already
            # assert against, same as a real LLM producing nothing useful.
            return ""
        if prompt_id.endswith("response") or "synthesizer" in prompt_id:
            return synthesize_response(user)
        if "json schema" in joined or request.response_format == "json_schema":
            if "diagnosed mechanism" in joined and "treatment metric" in joined:
                # StrategyAgent.generate_options (agents/strategy/prompts.py
                # options_user) — no prompt_id is set on this request, so it
                # was falling through to classify_lookup_query()'s unrelated
                # JSON shape, which InterventionOptionsLLM can't parse. That
                # silently zeroed every prescriptive mission's strategy
                # artifact (source stayed "insufficient" even with a
                # retained hypothesis) whenever a chat model was configured.
                return json.dumps(_deterministic_intervention_options(user))
            if "treatment_metric" in joined and "hypotheses" in joined:
                return json.dumps({"hypotheses": []})
            return json.dumps(classify_lookup_query(query, timezone, as_of))
        return "pong"


def _deterministic_intervention_options(user: str) -> dict[str, Any]:
    """One scripted InterventionOption for the fake LLM path -- proves the
    StrategyAgent -> Skeptic -> synthesis chain end to end without a real
    reasoning model, same spirit as ``classify_lookup_query``'s stand-ins."""
    mechanism = _extract_field(user, "Diagnosed mechanism") or "the diagnosed driver"
    treatment = _extract_field(user, "Treatment metric") or "the treatment metric"
    return {
        "options": [
            {
                "action": f"Address {mechanism} by adjusting {treatment}.",
                "mechanism_fit": "medium",
                "expected_impact": "Directional improvement in the outcome metric.",
                "cost": "low",
                "risk": "low",
                "reversibility": "high",
                "rationale": f"Targets the retained hypothesis linking {treatment} to the outcome.",
            }
        ]
    }

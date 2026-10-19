"""Fast query enhancer — rewrite ambiguous drafts into system-ready questions.

One short helper-model call. No agent loop, no Cube query. The composer uses
this so the user can normalize a messy draft before sending.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from seleric_swarm.config.settings import helper_chat_model
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMRequestMetadata

if TYPE_CHECKING:
    from seleric_swarm.runtime import SwarmRuntime

_log = logging.getLogger("seleric.services.query_enhance")

_SYSTEM_PROMPT = """\
You rewrite messy business-analytics questions so Seleric can execute them reliably.

Output rules:
- Return ONLY the rewritten question — no quotes, no preamble, no explanation.
- Keep the user's intent. Do not invent entities, brands, campaigns, or SKUs they did not name.
- Fix grammar and resolve ambiguity (what to measure, how to rank, which grain).
- Prefer catalogue metric names from the vocabulary when they fit (e.g. net sales, returned units, ad spend, ROAS).
- Prefer catalogue dimensions when the user means a breakdown (product, product_variant / SKU, channel, platform, campaign).
- Name a time window when ranking, comparing, or diagnosing. If the user named none, use "last 7 days".
- Prefer one clear question. Rank/order language when they ask for highest/lowest/top/worst.
- Keep it concise — usually one sentence, two at most.
- Vague "performance" / "how are we doing" asks: pick a small set of outcome + efficiency measures
  (e.g. ad spend, net sales, ROAS, orders) — do not dump a long marketing KPI checklist.

Examples:
- "which are the products with are highest return which variants"
  → "Which product variants had the highest returned units in the last 7 days? Rank by returned units descending."
- "give me ad performance"
  → "How did ad spend, net sales, and ROAS perform in the last 7 days?"
- "sales meta vs google"
  → "Compare Meta vs Google ad spend and net sales for the last 7 days."
- "why drop"
  → "Why did net sales drop yesterday compared to the prior 7 days?"
"""


def _metric_vocabulary(runtime: SwarmRuntime, *, limit: int = 60) -> str:
    """Compact metric name list so the rewrite stays within known measures."""
    lines: list[str] = []
    bootstrap = getattr(runtime, "bootstrap", None)
    if bootstrap is not None:
        try:
            rendered = bootstrap.snapshot().render_compact()
            if rendered:
                return rendered
        except Exception:
            _log.debug("catalogue snapshot unavailable for query enhance", exc_info=True)

    registry = getattr(runtime, "metrics", None)
    if registry is None:
        return ""
    for definition in list(registry.all())[:limit]:
        key = definition.catalogue_metric or definition.id.removeprefix("metric.")
        aliases = sorted({*(definition.aliases or []), key.replace("_", " ")})
        aka = f" (aka {', '.join(aliases[:4])})" if aliases else ""
        lines.append(f"- {key}{aka}")
    if not lines:
        return ""
    return "Known metrics:\n" + "\n".join(lines)


def _clean_enhanced(text: str, original: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r'^["\'“”‘’]+|["\'“”‘’]+$', "", cleaned).strip()
    # Drop accidental labels the model sometimes prefixes.
    cleaned = re.sub(
        r"^(enhanced|rewritten|normalized|query)\s*:\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    if not cleaned:
        return original.strip()
    # Collapse whitespace but keep intentional newlines rare; prefer one line.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class QueryEnhanceError(RuntimeError):
    """Raised when the helper model cannot produce a usable rewrite."""


async def enhance_query(
    runtime: SwarmRuntime,
    *,
    query: str,
    request_id: str | None = None,
) -> str:
    """Rewrite *query* into a clearer analytics question.

    Raises ``QueryEnhanceError`` when the helper call fails or returns empty
    visible text (reasoning models can burn a small token budget with no output).
    """
    original = (query or "").strip()
    if not original:
        return ""

    settings = runtime.settings
    model = helper_chat_model(settings) or getattr(settings, "primary_model", lambda: "")()
    if not model:
        raise QueryEnhanceError("no chat model configured for query enhance")

    vocab = _metric_vocabulary(runtime)
    user_parts = [f"Draft question:\n{original}"]
    if vocab:
        user_parts.append(vocab)
    user_parts.append("Rewrite the draft now.")

    try:
        response = await runtime.llm.complete(
            LLMRequest(
                messages=[
                    ChatMessage(role="system", content=_SYSTEM_PROMPT),
                    ChatMessage(role="user", content="\n\n".join(user_parts)),
                ],
                model=model,
                temperature=0,
                # gpt-4o-code / gpt-5* spend hidden reasoning tokens against this
                # budget — 220 finished with empty visible text (live 2026-10-10).
                max_tokens=1500,
                # Snappy composer feedback — do not wait for the full agent timeout.
                timeout_s=min(20.0, float(getattr(settings, "llm_timeout_s", 30.0) or 30.0)),
                metadata=LLMRequestMetadata(
                    request_id=request_id,
                    agent_id="query_enhance",
                    query_class="query_enhance",
                ),
                tags=["query_enhance"],
            )
        )
    except QueryEnhanceError:
        raise
    except Exception as exc:
        _log.warning("query_enhance_failed", exc_info=True)
        raise QueryEnhanceError("query enhance model call failed") from exc

    enhanced = _clean_enhanced(response.text, original)
    if not (response.text or "").strip():
        raise QueryEnhanceError("query enhance returned empty text")
    return enhanced

"""OpenRouter provider seam — an independent-provider fallback tier.

OpenRouter is OpenAI-compatible, so it reaches pydantic-ai through the same
``OpenAIProvider`` Azure uses, but with a plain ``AsyncOpenAI(base_url, api_key)``
client. It must NOT go through ``AzureOpenAICompatibleAdapter``, which forces the
Azure-only ``api-version`` query param and ``/models`` base path.

Kept in its own module (not inlined in ``agent/model.py``) so the LLM gateway can
adopt the same provider factory later without a rewrite.
"""

from __future__ import annotations

import json
from typing import Any

from seleric_swarm.config.secrets import resolve_secret
from seleric_swarm.config.settings import Settings


def resolved_openrouter_models(settings: Settings) -> list[str]:
    """Ordered OpenRouter model ids (first = highest priority).

    Reads ``OPENROUTER_MODELS`` as a JSON array or a comma-separated string,
    mirroring ``Settings.resolved_models()``. Empty/unset -> ``[]``.
    """
    raw = (getattr(settings, "openrouter_models", "") or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = []
        ids = [str(m).strip() for m in parsed if str(m).strip()]
        if ids:
            return ids
    return [m.strip() for m in raw.split(",") if m.strip()]


def build_openrouter_provider(settings: Settings) -> Any:
    """An ``OpenAIProvider`` backed by a plain OpenRouter client.

    Reuses ``resolve_secret`` so a deployed Key Vault resolves the key exactly
    as the Azure key does. Raises if the key is unset (callers gate on
    ``resolved_openrouter_models`` + key presence before calling).
    """
    from openai import AsyncOpenAI
    from pydantic_ai.providers.openai import OpenAIProvider

    api_key = resolve_secret("OPENROUTER_API_KEY", settings, settings.openrouter_api_key)
    if not api_key.strip():
        raise ValueError("OPENROUTER_API_KEY is not set")
    retries = max(3, getattr(settings, "llm_max_retries", 3))
    client = AsyncOpenAI(
        base_url=(settings.openrouter_endpoint or "https://openrouter.ai/api/v1").rstrip("/"),
        api_key=api_key,
        timeout=settings.llm_timeout_s,
        max_retries=retries,
        default_headers={"X-Title": "Seleric"},
    )
    return OpenAIProvider(openai_client=client)

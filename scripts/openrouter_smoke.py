"""Live OpenRouter connectivity gate — run BEFORE trusting the fallback tier.

Loads .env, builds the same provider resolve_v3_model uses, and does one minimal
completion against the first OPENROUTER_MODELS entry. Prints the reply + resolved
model id, or the provider error. Exit 0 = creds + connectivity good.

    .\\.venv\\Scripts\\python.exe scripts\\openrouter_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


async def main() -> int:
    from dotenv import load_dotenv
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel

    from seleric_swarm.config.settings import get_settings
    from seleric_swarm.llm.openrouter import (
        build_openrouter_provider,
        resolved_openrouter_models,
    )

    load_dotenv(ROOT / ".env")
    get_settings.cache_clear()
    settings = get_settings()

    models = resolved_openrouter_models(settings)
    if not models:
        print("FAIL: OPENROUTER_MODELS is not set in .env")
        return 2
    if not settings.openrouter_api_key.strip():
        print("FAIL: OPENROUTER_API_KEY is not set in .env")
        return 2

    model_name = models[0]
    print(f"Testing OpenRouter model: {model_name} via {settings.openrouter_endpoint}")
    provider = build_openrouter_provider(settings)
    agent = Agent(OpenAIChatModel(model_name, provider=provider))
    try:
        result = await agent.run("Reply with the single word: pong")
    except Exception as exc:  # noqa: BLE001 - surface the provider error verbatim
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    print(f"OK: reply={result.output!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

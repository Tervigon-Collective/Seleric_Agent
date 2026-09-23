"""Probe whether a 429 on one Azure AI Inference model deployment also hits a sibling
deployment on the same endpoint — i.e. whether the throttle is per-endpoint or per-model.

Usage:
    set AZURE_OPENAI_API_KEY=...          (PowerShell: $env:AZURE_OPENAI_API_KEY = "...")
    python scripts/check_rate_limit_scope.py

Fires both configured models back to back and prints status codes / retry-after headers.
Never hardcode the key here — always read it from the environment.
"""

from __future__ import annotations

import os
import sys
import time

from openai import OpenAI, RateLimitError

ENDPOINT = "https://llama4-maverick-prod-resource.services.ai.azure.com/openai/v1/"
MODELS = ["DeepSeek-V4-Flash", "gpt-5-mini"]


def probe(client: OpenAI, model: str) -> None:
    started = time.perf_counter()
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "What is the capital of France?"}],
        )
        elapsed = time.perf_counter() - started
        print(f"{model}: 200 OK in {elapsed:.2f}s -> {completion.choices[0].message.content!r}")
    except RateLimitError as exc:
        response = exc.response
        retry_after = response.headers.get("retry-after") if response is not None else None
        print(f"{model}: 429 rate-limited (retry-after={retry_after})")
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic script
        print(f"{model}: {type(exc).__name__}: {exc}")


def main() -> int:
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    if not api_key:
        print("Set AZURE_OPENAI_API_KEY in your environment first.", file=sys.stderr)
        return 1

    client = OpenAI(base_url=ENDPOINT, api_key=api_key)
    for model in MODELS:
        probe(client, model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

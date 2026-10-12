"""Per-mission LLM usage and cost for ``trace["usage"]``.

The agent loop's pydantic-ai ``RunUsage`` was enforced against ``UsageLimits``
but never exported, so legacy missions showed tokens as n/a and cost could not
be compared with the v2 stages. This turns the counter into a plain dict.

Cost is computed only from ``config/llm_prices.yaml``. A model with no listed
price yields ``cost_usd = None``; prices are never guessed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root

USAGE_KEY = "run_usage"


def load_prices(path: Path | None = None) -> dict[str, dict[str, float]]:
    """``{model: {input_per_mtok, output_per_mtok}}``; missing file or entries -> empty."""
    target = path or repo_root() / "config" / "llm_prices.yaml"
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    models = raw.get("models") if isinstance(raw, dict) else None
    out: dict[str, dict[str, float]] = {}
    for name, entry in (models or {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            out[str(name)] = {
                "input_per_mtok": float(entry["input_per_mtok"]),
                "output_per_mtok": float(entry["output_per_mtok"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def usage_trace(
    run_usage: Any,
    *,
    model: str | None = None,
    prices: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any] | None:
    """Plain-dict usage for the trace, or ``None`` when no usage was recorded."""
    if run_usage is None:
        return None
    input_tokens = int(getattr(run_usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(run_usage, "output_tokens", 0) or 0)
    price = (prices if prices is not None else load_prices()).get(model or "")
    cost = (
        round(
            (input_tokens * price["input_per_mtok"] + output_tokens * price["output_per_mtok"])
            / 1_000_000,
            6,
        )
        if price
        else None
    )
    return {
        "requests": int(getattr(run_usage, "requests", 0) or 0),
        "tool_calls": int(getattr(run_usage, "tool_calls", 0) or 0),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "model": model,
        "cost_usd": cost,
    }

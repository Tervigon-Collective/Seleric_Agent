"""Leadership hysteresis helpers."""

from __future__ import annotations

from typing import Any


def recent_target_blocked(history: list[dict[str, Any]], target: str, *, window: int = 2) -> bool:
    recent = [h.get("to_agent") for h in history[-window:]]
    return target in recent


def score_delta_ok(current: float, proposed: float, *, min_delta: float = 0.15) -> bool:
    return (proposed - current) >= min_delta

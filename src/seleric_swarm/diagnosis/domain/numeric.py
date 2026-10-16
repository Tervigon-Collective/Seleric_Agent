"""Numeric helpers the engine shares: the daily series type and the finite / median / mean / sign / relative-change
helpers that tolerate missing and non-finite values (moved from ``causal/diagnosis.py``, plan P2.2)."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, TypeGuard

Series = dict[date, float]


def _finite(v: Any) -> TypeGuard[float]:
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return math.nan
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else math.nan


def _sign(x: float) -> int:
    return 0 if abs(x) < 1e-12 else (1 if x > 0 else -1)


def _rel(delta: float, base: float) -> float | None:
    return delta / abs(base) if abs(base) > 1e-12 else None

"""Compare two JSON-like values: exact for everything but numbers, relative tolerance for numbers.

A causal estimate's last digits depend on the process and numerical libraries it ran in (a worker process and the main
one differ at 1e-9 relative on the same data), so a fixture check compares structure, text and booleans exactly and
numbers within a stated tolerance. The differences come back as short path-prefixed lines, first ones first."""

from __future__ import annotations

import math
from typing import Any


def _number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def differences(a: Any, b: Any, tol: float = 0.0, path: str = "") -> list[str]:
    if _number(a) and _number(b):
        if a == b or (math.isnan(a) and math.isnan(b)):
            return []
        if abs(a - b) <= tol * max(1.0, abs(a), abs(b)):
            return []
        return [f"{path or '/'}: {a!r} != {b!r}"]
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f"{path}/{key}: only in {'expected' if key in a else 'actual'}")
            else:
                out += differences(a[key], b[key], tol, f"{path}/{key}")
        return out
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return [f"{path or '/'}: length {len(a)} != {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in differences(x, y, tol, f"{path}[{i}]")]
    same = a == b and isinstance(a, bool) == isinstance(b, bool)  # True == 1 in Python; here they differ
    return [] if same else [f"{path or '/'}: {str(a)[:120]!r} != {str(b)[:120]!r}"]

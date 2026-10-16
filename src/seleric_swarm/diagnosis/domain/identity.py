"""A data-verified accounting identity: ``outcome = k x product(factor ^ exponent)`` (moved from
``causal/diagnosis.py``, plan P2.2)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Identity:
    outcome: str
    factors: list[tuple[str, float]]  # (metric_id, exponent)
    k: float
    dispersion: float
    n_days: int
    approximate: bool = False

    def value(self, xs: list[float]) -> float:
        out = self.k
        for (_, e), x in zip(self.factors, xs, strict=True):
            if x == 0 and e < 0:
                return math.nan
            out *= x**e
        return out

    def describe(self) -> str:
        num = [m for m, e in self.factors if e > 0]
        den = [m for m, e in self.factors if e < 0]
        rhs = " × ".join(num) or "1"
        if den:
            rhs += " ÷ " + " ÷ ".join(den)
        if not math.isclose(self.k, 1.0, rel_tol=1e-3):
            rhs = f"{self.k:.6g} × " + rhs
        return f"{self.outcome} {'≈' if self.approximate else '='} {rhs}"

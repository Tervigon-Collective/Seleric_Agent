"""Real execution-limit enforcement (Sprint 2 Profile A).

Supersedes ``coordinator/governance/budget.py``'s ``check_budget``/
``check_hard_stops`` — both permanently disabled no-ops (see that module's
docstring: "nothing in the system rejects or truncates a mission for
LLM/tool/agent-call/runtime spend any more"). ``ExecutionBudgetTracker.consume``
is the first budget concept in this system that actually rejects once a
counter would cross its limit, rather than always reporting ok.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from seleric_swarm.agent.dependencies import ExecutionLimits


@dataclass
class BudgetVerdict:
    ok: bool
    error_code: str | None = None
    reason: str | None = None
    exhausted_key: str | None = None


_OK = BudgetVerdict(ok=True)

# Counter attribute name -> the ExecutionLimits field that bounds it.
_COUNTERS = ("tool_calls", "cube_queries", "causal_queries", "prediction_calls", "validation_revisions")


@dataclass
class ExecutionBudgetTracker:
    """Mutable per-mission-run counters checked against ``SelericDeps.limits``."""

    limits: ExecutionLimits
    tool_calls: int = 0
    cube_queries: int = 0
    causal_queries: int = 0
    prediction_calls: int = 0
    validation_revisions: int = 0
    _started_at: float = field(default_factory=time.monotonic, repr=False)

    def consume(self, key: str, *, amount: int = 1) -> BudgetVerdict:
        if key not in _COUNTERS:
            raise ValueError(f"unknown budget counter: {key!r}")
        current = getattr(self, key)
        limit = getattr(self.limits, f"max_{key}")
        if current + amount > limit:
            return BudgetVerdict(
                ok=False,
                error_code="EXECUTION_LIMIT_EXCEEDED",
                reason=f"{key} would exceed its limit ({limit})",
                exhausted_key=key,
            )
        setattr(self, key, current + amount)
        return _OK

    def check_runtime(self) -> BudgetVerdict:
        elapsed = time.monotonic() - self._started_at
        if elapsed > self.limits.max_runtime_seconds:
            return BudgetVerdict(
                ok=False,
                error_code="EXECUTION_LIMIT_EXCEEDED",
                reason=f"mission runtime ({elapsed:.1f}s) exceeded max_runtime_seconds ({self.limits.max_runtime_seconds}s)",
                exhausted_key="runtime_seconds",
            )
        return _OK

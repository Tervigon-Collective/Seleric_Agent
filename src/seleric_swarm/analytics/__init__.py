"""Pure analytics functions over already-fetched evidence (Profile C).

Non-negotiable rule 5: nothing in this package fetches data. Every function
takes evidence the Semantic toolset already retrieved and returns a
computation over it. There are no I/O imports here on purpose — if a module
in this package ever needs an MCP client, a ``BusinessStateService`` or a
``Blackboard``, that is the rule-5 boundary being crossed, not a missing
convenience.

``toolsets/analytics.py`` is the thin agent-facing wrapper over these
functions; this package is where the arithmetic lives so it can be unit
tested without a ``RunContext``, an artifact store or an event loop.

See ``docs/refactor/03_PROFILE_CAPABILITIES.md`` §3 and §10.
"""

from seleric_swarm.analytics.comparison import period_deltas
from seleric_swarm.analytics.grain import (
    CALCULATION_VERSION,
    period_span_days,
    validate_grain_set,
)

__all__ = [
    "CALCULATION_VERSION",
    "period_deltas",
    "period_span_days",
    "validate_grain_set",
]

"""Registry metric_id → live catalogue measure id.

Sprint 2 consolidation (docs/refactor/SPRINT_PLAN.md, 2026-09-18): this
module used to also own ``resolve_measure()``, a keyword-overlap
catalogue-search fallback for when a registry's ``catalogue_metric`` id was
stale — exactly the heuristic layer identified as bug #8's root cause. It's
retired with zero remaining callers (every fetch path now uses
``MetricDefinition.catalogue_metric`` directly via
``toolsets/semantic.py::raw_query_metric()``, no substitution). Only
``module_args()`` (a static config-field read, not a heuristic) survives.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.services.metrics import MetricDefinition


def module_args(definition: MetricDefinition) -> dict[str, Any]:
    if "seleric_module" in definition.raw:
        return {"module": definition.seleric_module}
    return {}

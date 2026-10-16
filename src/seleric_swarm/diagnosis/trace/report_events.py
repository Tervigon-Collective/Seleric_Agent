"""The events a finished diagnosis report gives rise to (plan §5.2): one resolved hypothesis per driver the engine
tested, with its refuters, and the unexplained share. Shared by the live tool's trace and the replayable runner, so the
two cannot describe the same report differently."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from seleric_swarm.diagnosis.domain.report import DiagnosisReport
from seleric_swarm.diagnosis.trace import events as kinds

Emit = Callable[[str, Mapping[str, Any], int | None], int | None]


def emit_report_events(emit: Emit, report: DiagnosisReport) -> None:
    """``emit(kind, payload, parent)`` returns the sequence number of the event it wrote (or ``None``)."""
    for driver in report.drivers:
        head = emit(
            kinds.HYPOTHESIS_RESOLVED,
            {
                "hypothesis": driver.driver, "status": driver.status,
                "label": getattr(driver.classification, "value", None) or str(driver.classification or ""),
                "p_raw": driver.p_value, "share_of_change": driver.share_of_change, "n_rows": driver.n_rows,
            },
            None,
        )
        for refutation in driver.refutations:
            emit(
                kinds.REFUTER_RESULT,
                {
                    "hypothesis": driver.driver, "refuter": str(refutation.get("name", "")),
                    "passed": bool(refutation.get("passed")), "error": str(refutation.get("error", ""))[:200],
                },
                head,
            )
    for found in report.measurement:
        emit(
            kinds.HYPOTHESIS_RESOLVED,
            {
                "hypothesis": f"measurement:{found.metric}~{found.peer}",
                "status": {"diverged": "supported", "agrees": "rejected"}.get(found.status, "inconclusive"),
                "label": "measurement", "z": found.z, "gap": found.gap, "n_rows": found.n_history,
            },
            None,
        )
    if report.unexplained_share is not None:
        emit(kinds.UNEXPLAINED_SHARE_UPDATED, {"share": report.unexplained_share}, None)

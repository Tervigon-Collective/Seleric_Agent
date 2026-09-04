"""Contradiction engine (spec sec. 21).

Searches loaded + related evidence and attached artifacts for statements that
conflict with the claim or with each other, and classifies each conflict. A
same-definition, same-window numeric disagreement is a factual/data
contradiction (blocking); a cross-source disagreement with compatible
definitions is a source conflict (warning + reconciliation follow-up).
"""

from __future__ import annotations

from itertools import combinations

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge, followup

_REL_TOL = 0.05


class ContradictionValidator(Validator):
    name = "contradiction"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        rows = ctx.all_evidence()
        found: list[dict] = []

        by_metric: dict[str, list] = {}
        for ev in rows:
            if ev.metric_id and _numeric(ev.value) is not None:
                by_metric.setdefault(ev.metric_id, []).append(ev)

        for metric_id, group in by_metric.items():
            for a, b in combinations(group, 2):
                va, vb = _numeric(a.value), _numeric(b.value)
                if va is None or vb is None:
                    continue
                if _close(va, vb):
                    continue
                same_window = (a.time_range or {}) == (b.time_range or {}) or (
                    a.time_range.get("start") == b.time_range.get("start")
                    and a.time_range.get("end") == b.time_range.get("end")
                )
                same_dims = (a.dimensions or {}) == (b.dimensions or {})
                same_defn = (a.calculation_version or None) == (b.calculation_version or None) and (
                    a.unit == b.unit
                )
                diff_source = bool(a.source) and bool(b.source) and a.source != b.source

                if not same_dims:
                    # different segment slices of one metric legitimately differ
                    continue
                if not same_window:
                    ctype, sev = "time_range_conflict", "info"
                elif not same_defn:
                    # definition differs -> metric validator owns this, don't double-flag
                    continue
                elif diff_source:
                    ctype, sev = "source_conflict", "warning"
                else:
                    ctype, sev = "factual_conflict", "blocking"

                found.append(
                    {
                        "type": ctype,
                        "metric_id": metric_id,
                        "rows": [a.evidence_id, b.evidence_id],
                        "values": [va, vb],
                        "sources": [a.source, b.source],
                    }
                )
                out.challenges.append(
                    challenge(
                        "contradiction" if ctype == "factual_conflict" else "source" if ctype == "source_conflict" else "temporal",
                        sev,  # type: ignore[arg-type]
                        f"{ctype} on {metric_id}: {va} vs {vb} (sources {a.source or '?'} / {b.source or '?'}).",
                        evidence_refs=[a.evidence_id, b.evidence_id],
                        detail={"contradiction_type": ctype},
                        remediation_hint="Reconcile the disagreeing sources before relying on this number."
                        if ctype == "source_conflict"
                        else None,
                    )
                )
                if ctype == "factual_conflict":
                    out.status = "REJECTED"
                elif ctype == "source_conflict":
                    out.status = _weaken(out.status)
                    out.followups.append(
                        followup(
                            "cross_source_reconciliation",
                            f"Reconcile conflicting {metric_id} values across sources.",
                            f"Which source is authoritative for {metric_id} in this window, and why do "
                            f"{a.source or 'source A'} and {b.source or 'source B'} disagree?",
                            evidence_refs=[a.evidence_id, b.evidence_id],
                            priority=8,
                        )
                    )

        # explicit contradiction refs on the claim
        for ref in ctx.claim.contradiction_refs:
            resolved = next((e for e in rows if e.evidence_id == ref), None)
            found.append({"type": "factual_conflict", "ref": ref, "resolved": bool(resolved)})
            out.status = "REJECTED"
            out.challenges.append(
                challenge(
                    "contradiction",
                    "blocking",
                    f"Claim declares contradiction ref {ref}; treat as a live conflict until resolved.",
                    evidence_refs=[ref],
                )
            )

        if out.status == "OK":
            out.score_signals["cross_source_agreement"] = 0.85 if by_metric else 0.6
        elif out.status == "WEAK":
            out.score_signals["cross_source_agreement"] = 0.4
        else:
            out.score_signals["cross_source_agreement"] = 0.05
        out.detail = {"contradictions": found}
        return out


def _numeric(value) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _close(a: float, b: float) -> bool:
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= _REL_TOL


def _weaken(status: str) -> str:
    return "WEAK" if status == "OK" else status

"""Provenance validator (spec sec. 19).

Checks that each evidence row can be traced back to a source, a tool/MCP call,
a query/argument hash, a retrieval timestamp and the metric/calculation version
that produced it. Requirements are per claim type via policy.
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge


class ProvenanceValidator(Validator):
    name = "provenance"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        if not ctx.evidence:
            out.status = "NOT_APPLICABLE"
            out.score_signals["provenance_completeness"] = 0.0
            return out

        need_hash = ctx.claim.claim_type in ctx.policies.require_query_hash_for()
        need_calc = ctx.claim.claim_type in ctx.policies.require_calculation_version_for()

        missing_source, missing_hash, missing_calc, missing_ts = [], [], [], []
        for ev in ctx.evidence:
            if not ev.source:
                missing_source.append(ev.evidence_id)
            if need_hash and not ev.query_hash:
                missing_hash.append(ev.evidence_id)
            if need_calc and not (ev.calculation_version or ev.source_version):
                missing_calc.append(ev.evidence_id)
            if not (ev.retrieved_at or ev.freshness):
                missing_ts.append(ev.evidence_id)

        complete = len(ctx.evidence)
        deductions = 0
        for label, ids, sev in (
            ("source", missing_source, "blocking"),
            ("query/tool hash", missing_hash, "warning"),
            ("calculation/metric version", missing_calc, "warning"),
            ("retrieval timestamp", missing_ts, "warning"),
        ):
            if ids:
                deductions += len(ids)
                out.challenges.append(
                    challenge(
                        "provenance",
                        sev,  # type: ignore[arg-type]
                        f"{len(ids)} evidence row(s) missing {label}: {ids}",
                        evidence_refs=ids,
                        remediation_hint=f"Re-retrieve with full provenance ({label}).",
                    )
                )
                if sev == "blocking":
                    out.status = "REJECTED"

        if out.status != "REJECTED":
            out.status = "OK" if deductions == 0 else "WEAK"
        out.score_signals["provenance_completeness"] = max(0.0, 1.0 - deductions / max(1, complete * 3))
        out.detail = {
            "missing_source": missing_source,
            "missing_hash": missing_hash,
            "missing_calc_version": missing_calc,
            "missing_timestamp": missing_ts,
        }
        return out

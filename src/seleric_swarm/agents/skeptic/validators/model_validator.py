"""Model validator (spec sec. 28-29).

Audits the model behind a :class:`ForecastArtifact` against the injected
:class:`ModelRegistry`: existence, approval status, target match, applicability,
recent validation, drift status, backtest availability, interval presence.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge, gap

_STATUS_SCORE = {
    "MODEL_VALID": 0.95,
    "MODEL_DEGRADED": 0.55,
    "MODEL_OUT_OF_DOMAIN": 0.25,
    "MODEL_DRIFTED": 0.1,
    "MODEL_METADATA_INCOMPLETE": 0.3,
    "MODEL_REJECTED": 0.0,
}


class ModelValidator(Validator):
    name = "model"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        if not ctx.forecasts:
            out.status = "NOT_APPLICABLE"
            return out

        fc = ctx.forecasts[0]
        verdict = "MODEL_VALID"
        issues: list[str] = []

        if not fc.model_id or not fc.model_version:
            verdict = "MODEL_METADATA_INCOMPLETE"
            issues.append("model id/version missing on the forecast artifact")
            out.evidence_gaps.append(
                gap(
                    "Forecast has no model id/version.",
                    "A forecast without model lineage cannot be trusted or reproduced.",
                    capability_required="model_registry_lookup",
                    blocking=False,
                    priority=8,
                )
            )
        else:
            record = ctx.deps.model_registry.get(fc.model_id)
            if record is None:
                verdict = "MODEL_METADATA_INCOMPLETE"
                issues.append(f"model '{fc.model_id}' not found in the registry")
            else:
                if record.status not in {"approved", "production"}:
                    verdict = "MODEL_DEGRADED"
                    issues.append(f"model status is '{record.status}', not approved")
                if record.target and fc.target_metric and record.target != fc.target_metric:
                    verdict = "MODEL_OUT_OF_DOMAIN"
                    issues.append(f"model target {record.target} != forecast target {fc.target_metric}")
                hist = (ctx.claim.metadata.get("available_history_days")
                        or ctx.risk_context.get("available_history_days"))
                if hist is not None and record.minimum_history_days and hist < record.minimum_history_days:
                    verdict = "MODEL_OUT_OF_DOMAIN"
                    issues.append(f"available history {hist}d < required {record.minimum_history_days}d")
                if ctx.policies.model_require_backtest() and not (record.backtest_available or fc.backtest_metrics):
                    verdict = "MODEL_DEGRADED" if verdict == "MODEL_VALID" else verdict
                    issues.append("no backtest metrics available")
                if record.last_validated_at:
                    age = _age_days(record.last_validated_at)
                    if age is not None and age > ctx.policies.model_recent_validation_days():
                        verdict = "MODEL_DEGRADED" if verdict == "MODEL_VALID" else verdict
                        issues.append(f"model last validated {age}d ago")

        # drift: prefer the artifact's stamped status; else consult a live monitor
        drift_status = fc.drift_status
        drift_source = "artifact"
        if (not drift_status or drift_status.lower() in {"unknown", ""}) and fc.model_id:
            try:
                report = await ctx.deps.drift_monitor.status_for(
                    fc.model_id, features={**ctx.risk_context, **ctx.claim.metadata}
                )
                drift_status = report.status
                drift_source = "monitor"
                out.detail["drift_signals"] = report.signals
            except Exception as exc:  # a monitor outage is a warning, not a crash
                out.methodological_issues.append(f"drift monitor unavailable: {exc}")
        drift = (drift_status or "").lower()
        if drift in ctx.policies.drift_reject_statuses():
            verdict = "MODEL_DRIFTED"
            issues.append(f"drift status '{drift_status}' (via {drift_source})")
        elif drift in {"unknown", ""}:
            issues.append("drift status unknown (no monitor / stale)")
            if verdict == "MODEL_VALID":
                verdict = "MODEL_DEGRADED"

        if verdict in {"MODEL_DRIFTED", "MODEL_REJECTED", "MODEL_OUT_OF_DOMAIN"}:
            out.status = "REJECTED"
            out.challenges.append(challenge("model", "blocking", f"{verdict}: {issues}", evidence_refs=[fc.forecast_id]))
        elif verdict in {"MODEL_DEGRADED", "MODEL_METADATA_INCOMPLETE"}:
            out.status = "WEAK"
            out.challenges.append(challenge("model", "warning", f"{verdict}: {issues}", evidence_refs=[fc.forecast_id]))

        out.score_signals["model_applicability"] = _STATUS_SCORE.get(verdict, 0.3)
        out.detail = {**out.detail, "verdict": verdict, "issues": issues, "drift_source": drift_source}
        return out


def _age_days(iso: str) -> int | None:
    normalized = f"{iso[:-1]}+00:00" if iso.endswith("Z") else iso
    try:
        dt = datetime.fromisoformat(normalized)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - dt).days
    except ValueError:
        return None

"""ModelToolset — predictions over already-fetched evidence (Profile C, Sprint 3).

Frozen signatures: ``docs/refactor/CONTRACTS.md`` §4 (three functions; A1.5
struck ``simulate``/``predict_reverse_risk``/``predict_demand``).

Rules 4 and 5 as everywhere else: no fetching, no calling another tool.
Rule 10: every ``PredictionArtifact`` carries ``model_id`` + ``model_version``.
Rule 20: ``feature_leakage_checked`` is set explicitly, never left implied.

The registry is the control, not decoration
-------------------------------------------
Every entry point resolves its target metric against
``config/model_registry.yaml`` and refuses unless an **approved** model exists
for that exact metric. That is why:

* ``forecast`` works — four approved daily forecast models are registered, and
  ``models/service.py`` implements them (exponential smoothing, deterministic,
  interval-producing);
* ``predict_ltv`` and ``predict_propensity`` refuse — this repo has no labelled
  training data and no feature store for either, so no approved model exists and
  none should. They return ``INSUFFICIENT_EVIDENCE`` with
  ``policy:no_approved_model`` rather than a fabricated number.

Those two are not stubs-to-fill-in-later; they are the registry gate working.
Implementing them means training a real model and registering it, at which
point the same code path starts serving them with no change here.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact, PredictionArtifact
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.analytics.grain import CALCULATION_VERSION, validate_grain_set
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.models.service import ForecastUnavailable, ModelRecord, forecast_series, model_registry_from_yaml
from seleric_swarm.toolsets import policy_config as policy

_registry_cache: dict[str, object] = {}


def _registry():
    """One parse of the YAML per process — it is a small static file."""
    if "r" not in _registry_cache:
        _registry_cache["r"] = model_registry_from_yaml()
    return _registry_cache["r"]


def _refuse(summary: str, *, warning: str, error_code: str = "INSUFFICIENT_EVIDENCE") -> ToolResult:
    return ToolResult(
        success=False, summary=summary, error_code=error_code, retryable=False, warnings=[warning]
    )


def _load_evidence(
    ctx: RunContext[SelericDeps], evidence_ids: list[str]
) -> tuple[list[EvidenceArtifact], ToolResult | None]:
    if not evidence_ids:
        return [], _refuse("no evidence_ids supplied", warning=policy.WARN_NO_EVIDENCE)

    artifacts = ctx.deps.artifact_store.get_many(list(evidence_ids))
    found = {a.id for a in artifacts}
    missing = [aid for aid in evidence_ids if aid not in found]
    if missing:
        return [], _refuse(
            f"evidence not found in store: {', '.join(missing)}", warning=policy.WARN_NO_EVIDENCE
        )

    evidence: list[EvidenceArtifact] = []
    for artifact in artifacts:
        if artifact.artifact_type != "evidence":
            return [], _refuse(
                f"artifact {artifact.id} is artifact_type={artifact.artifact_type!r}, not evidence",
                warning=policy.WARN_NO_EVIDENCE,
            )
        try:
            evidence.append(EvidenceArtifact.model_validate(artifact.payload))
        except Exception as exc:
            return [], _refuse(
                f"artifact {artifact.id} is not a valid EvidenceArtifact: {exc}",
                warning=policy.WARN_NO_EVIDENCE,
            )
    return evidence, None


def _approved_model_for(target: str, *, model_type: str) -> ModelRecord | None:
    for record in _registry().for_target(target):  # type: ignore[attr-defined]
        if record.status == "approved" and record.model_type == model_type:
            return record
    return None


def _write_prediction(
    ctx: RunContext[SelericDeps],
    *,
    record: ModelRecord,
    prediction_type: str,
    value: float,
    interval: tuple[float, float] | None,
    evidence_ids: list[str],
) -> str:
    prediction = PredictionArtifact(
        model_id=record.model_id,
        model_version=record.version,
        prediction_type=prediction_type,
        value=value,
        confidence_interval=interval,
        evidence_ids=list(evidence_ids),
        # True because the inputs are this metric's own prior observations and
        # nothing from the target period — the series ends before the horizon
        # begins. Asserted from how the evidence is assembled, not assumed.
        feature_leakage_checked=True,
    )
    artifact = ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type="prediction",
            payload=prediction.model_dump(mode="json"),
            classification="derived",
            evidence_ids=list(evidence_ids),
            provenance=ArtifactProvenance(
                evidence_ids=list(evidence_ids),
                calculation_version=CALCULATION_VERSION,
                model_version=f"{record.model_id}@{record.version}",
            ),
            mission_id=ctx.deps.mission_id,
        )
    )
    return artifact.id


async def forecast(
    ctx: RunContext[SelericDeps], evidence_ids: list[str], horizon_days: int
) -> ToolResult:
    """Forecast a metric ``horizon_days`` past its last observation.

    The evidence set is the history and also names the target: every row must
    be the same metric at the same grain, which the A1.2 precondition enforces
    for the same reason it does in Analytics — a window aggregate labelled
    ``grain="day"`` would silently become a "daily" history point
    (``docs/BUG_SHEET.md`` #14).
    """
    evidence, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    metrics = {e.metric_id for e in evidence}
    if len(metrics) != 1:
        return _refuse(
            f"forecast needs one metric's history, got {sorted(metrics)}",
            warning=policy.WARN_MODEL_TARGET_MISMATCH,
        )
    target = metrics.pop()

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        return ToolResult(
            success=False, summary=mismatch, error_code="EVIDENCE_GRAIN_MISMATCH", retryable=False
        )

    record = _approved_model_for(target, model_type="forecast")
    if record is None:
        return _refuse(
            f"no approved forecast model registered for {target}",
            warning=policy.WARN_NO_APPROVED_MODEL,
        )

    usable = [e for e in evidence if e.value is not None]
    if len(usable) < record.minimum_history_days:
        return _refuse(
            f"{record.model_id} requires {record.minimum_history_days} history point(s), "
            f"got {len(usable)}",
            warning=policy.WARN_THIN_HISTORY,
        )

    try:
        result = forecast_series(evidence, horizon_days=horizon_days, model_id=record.model_id)
    except ForecastUnavailable as exc:
        return _refuse(str(exc), warning=exc.warning)

    artifact_id = _write_prediction(
        ctx,
        record=record,
        prediction_type="forecast",
        value=result.value,
        interval=result.interval,
        evidence_ids=evidence_ids,
    )
    return ToolResult(
        success=True,
        artifact_ids=[artifact_id],
        summary=(
            f"{target} forecast +{horizon_days}d: {result.value:.6g} "
            f"[{result.interval[0]:.6g}, {result.interval[1]:.6g}] "
            f"({result.method}, {result.history_points} history points)"
        ),
        provenance=ArtifactProvenance(
            evidence_ids=list(evidence_ids),
            model_version=f"{record.model_id}@{record.version}",
        ),
        warnings=result.warnings,
    )


async def predict_ltv(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult:
    """Customer lifetime value. **No approved model exists** — see module docstring.

    LTV needs per-customer histories and a survival/margin model; this repo has
    aggregate metric evidence only. Refusing is the correct answer, not a
    placeholder for one.
    """
    return _refuse(
        "no approved LTV model is registered; LTV requires per-customer training data "
        "and a feature store, neither of which exists in this deployment",
        warning=policy.WARN_NO_APPROVED_MODEL,
    )


async def predict_propensity(
    ctx: RunContext[SelericDeps], evidence_ids: list[str], event: str
) -> ToolResult:
    """Propensity to convert on ``event``. **No approved model exists.**

    Propensity needs labelled per-subject outcomes; aggregate metric evidence
    cannot produce one, and a number derived from it would be fabricated.
    """
    return _refuse(
        f"no approved propensity model is registered for {event!r}; propensity requires "
        "labelled per-subject outcomes, which this deployment does not collect",
        warning=policy.WARN_NO_APPROVED_MODEL,
    )

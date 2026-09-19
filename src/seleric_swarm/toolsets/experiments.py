"""ExperimentToolset — history, sizing, evaluation (Profile C, Sprint 4).

Frozen signatures: ``docs/refactor/CONTRACTS.md`` §4, three functions. A1.5
struck ``design_experiment``/``compare_variants``/``recommend_next_test``;
they are not implemented here and should not be added without re-proposing.

``estimate_sample_size`` is pure statistics and needs nothing but its
arguments. The other two read ``config/experiment_registry.yaml`` — the record
of what was actually run. ``evaluate_experiment`` refuses on an unknown id
rather than scoring whatever evidence it was handed: measuring "lift" against
an undeclared control is how a system ends up asserting a result nobody
designed.

One trap avoided deliberately: ``agents/skeptic/registries.py::
DeterministicStatsValidator.check()`` returns a **passing** ``StatCheck`` for
any check name it does not recognize. Nothing here routes through it, so no
gate gets a free green light.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.experiments.registry import Experiment, registry_from_yaml
from seleric_swarm.experiments.stats import SampleSizeUnavailable
from seleric_swarm.experiments.stats import estimate_sample_size as _estimate
from seleric_swarm.experiments.stats import lift as _lift
from seleric_swarm.toolsets import policy_config as policy

_CALCULATION_VERSION = "experiments.v1"

_registry_cache: dict[str, object] = {}


def _registry():
    """One parse per process — a small static file."""
    if "r" not in _registry_cache:
        _registry_cache["r"] = registry_from_yaml()
    return _registry_cache["r"]


def _refuse(summary: str, *, warning: str | None = None) -> ToolResult:
    return ToolResult(
        success=False,
        summary=summary,
        error_code="INSUFFICIENT_EVIDENCE",
        retryable=False,
        warnings=[warning] if warning else [],
    )


def _describe(experiment: Experiment) -> str:
    control = experiment.control
    return (
        f"{experiment.experiment_id} [{experiment.status}] {experiment.name} — "
        f"metric {experiment.metric_id or 'unset'}, "
        f"{len(experiment.variants)} variant(s)"
        f"{', control ' + control.name if control else ', NO CONTROL DECLARED'}"
    )


async def get_experiment_history(
    ctx: RunContext[SelericDeps], experiment_id: str | None = None
) -> ToolResult:
    """List registered experiments, or describe one.

    Returns no artifacts: a registry listing is not evidence for a numeric
    claim, and §2 permits the Experiment toolset to succeed with none.
    """
    registry = _registry()
    if len(registry) == 0:  # type: ignore[arg-type]
        return ToolResult(
            success=True,
            summary=(
                "no experiments are registered — config/experiment_registry.yaml is "
                "absent or empty. Nothing has been run; this is not a failed lookup."
            ),
            warnings=[policy.WARN_NO_EXPERIMENT_RECORDS],
            provenance=ArtifactProvenance(calculation_version=_CALCULATION_VERSION),
        )

    if experiment_id is not None:
        experiment = registry.get(experiment_id)  # type: ignore[attr-defined]
        if experiment is None:
            return _refuse(
                f"no experiment {experiment_id!r} is registered",
                warning=policy.WARN_UNKNOWN_EXPERIMENT,
            )
        detail = _describe(experiment)
        if experiment.hypothesis:
            detail += f"\nhypothesis: {experiment.hypothesis}"
        if experiment.notes:
            detail += f"\nnotes: {experiment.notes}"
        return ToolResult(
            success=True,
            summary=detail,
            provenance=ArtifactProvenance(calculation_version=_CALCULATION_VERSION),
        )

    rows = registry.all()  # type: ignore[attr-defined]
    return ToolResult(
        success=True,
        summary=f"{len(rows)} registered experiment(s):\n"
        + "\n".join(f"- {_describe(e)}" for e in rows),
        provenance=ArtifactProvenance(calculation_version=_CALCULATION_VERSION),
    )


async def estimate_sample_size(
    ctx: RunContext[SelericDeps],
    baseline_rate: float,
    mde: float,
    power: float = policy.DEFAULT_POWER,
) -> ToolResult:
    """Per-variant sample size for a two-proportion A/B test.

    ``mde`` is the **absolute** rate difference to detect — baseline 0.02 with
    ``mde=0.005`` sizes for 2.0% → 2.5%. Said explicitly in the summary too,
    because relative-vs-absolute is the usual way this number ends up silently
    several times wrong.
    """
    try:
        result = _estimate(baseline_rate, mde, power=power)
    except SampleSizeUnavailable as exc:
        return _refuse(str(exc), warning=exc.warning)

    return ToolResult(
        success=True,
        summary=(
            f"{result.per_variant:,} per variant ({result.total:,} total) to detect an "
            f"absolute change from {result.baseline_rate:.3%} to "
            f"{result.baseline_rate + result.mde:.3%} "
            f"at {result.power:.0%} power, alpha {result.alpha:.2f} "
            f"(Cohen's h {result.effect_size:.4f})"
        ),
        provenance=ArtifactProvenance(
            calculation_version=_CALCULATION_VERSION,
            source_metadata={
                "per_variant": result.per_variant,
                "total": result.total,
                "power": result.power,
                "alpha": result.alpha,
                "mde_is_absolute": True,
            },
        ),
    )


async def evaluate_experiment(
    ctx: RunContext[SelericDeps], experiment_id: str, evidence_ids: list[str]
) -> ToolResult:
    """Observed lift of each treatment against the declared control.

    The experiment must be registered and must declare a control. Evidence
    must be **dimension-stamped** with each variant's label, i.e. ``drilldown``
    output — a plain ``query_metrics`` breakdown leaves ``dimensions`` empty on
    every row, which would make the variants indistinguishable.

    Significance is **not** asserted. The evidence carries point values with
    no interval or sample count, so ``Finding.metrics`` reports lift and the
    summary says significance is unknown. Reporting "not significant" from
    data that cannot support either verdict would be the more dangerous
    answer.
    """
    registry = _registry()
    experiment = registry.get(experiment_id)  # type: ignore[attr-defined]
    if experiment is None:
        return _refuse(
            f"no experiment {experiment_id!r} is registered; refusing to score evidence "
            "against an undeclared design",
            warning=policy.WARN_UNKNOWN_EXPERIMENT,
        )
    control = experiment.control
    if control is None:
        return _refuse(
            f"{experiment_id} declares no control variant; lift is undefined without one",
            warning=policy.WARN_MISSING_VARIANT_EVIDENCE,
        )
    if not evidence_ids:
        return _refuse("no evidence_ids supplied", warning=policy.WARN_NO_EVIDENCE)

    artifacts = ctx.deps.artifact_store.get_many(list(evidence_ids))
    found = {a.id for a in artifacts}
    missing = [aid for aid in evidence_ids if aid not in found]
    if missing:
        return _refuse(
            f"evidence not found in store: {', '.join(missing)}",
            warning=policy.WARN_NO_EVIDENCE,
        )

    # Map each variant's declared label to its measured value.
    by_label: dict[str, tuple[str, float]] = {}
    for artifact in artifacts:
        if artifact.artifact_type != "evidence":
            return _refuse(
                f"artifact {artifact.id} is {artifact.artifact_type!r}, not evidence",
                warning=policy.WARN_NO_EVIDENCE,
            )
        try:
            evidence = EvidenceArtifact.model_validate(artifact.payload)
        except Exception as exc:
            return _refuse(
                f"artifact {artifact.id} is not a valid EvidenceArtifact: {exc}",
                warning=policy.WARN_NO_EVIDENCE,
            )
        if evidence.value is None:
            continue
        for label in evidence.dimensions.values():
            by_label[label] = (artifact.id, float(evidence.value))

    if control.label not in by_label:
        return _refuse(
            f"no evidence stamped with the control label {control.label!r}; "
            "evaluate_experiment needs drilldown output carrying each variant's dimension "
            "value, not a pooled query_metrics breakdown",
            warning=policy.WARN_MISSING_VARIANT_EVIDENCE,
        )

    control_ref, control_value = by_label[control.label]
    artifact_ids: list[str] = []
    warnings: list[str] = ["significance not assessed: evidence carries no interval or sample size"]

    for variant in experiment.treatments:
        if variant.label not in by_label:
            warnings.append(f"{policy.WARN_MISSING_VARIANT_EVIDENCE}:{variant.name}")
            continue
        variant_ref, variant_value = by_label[variant.label]
        result = _lift(
            variant=variant.name, control_value=control_value, variant_value=variant_value
        )
        metrics = {"control": control_value, "variant": variant_value, "absolute_lift": result.absolute}
        if result.relative is not None:
            metrics["relative_lift"] = result.relative
        relative_text = (
            f" ({result.relative:+.1%})" if result.relative is not None else " (relative undefined, control is 0)"
        )
        finding = Finding(
            finding_type="experiment_evaluation",
            statement=(
                f"{experiment_id} variant {variant.name}: {experiment.metric_id} "
                f"{variant_value:.4g} vs control {control_value:.4g}, "
                f"{result.absolute:+.4g}{relative_text}; significance unknown"
            ),
            evidence_ids=[control_ref, variant_ref],
            metrics=metrics,
        )
        stored = ctx.deps.artifact_store.put(
            Artifact(
                workspace_id=ctx.deps.principal.workspace_id,
                artifact_type="finding",
                payload=finding.model_dump(mode="json"),
                classification="derived",
                evidence_ids=[control_ref, variant_ref],
                provenance=ArtifactProvenance(
                    evidence_ids=[control_ref, variant_ref],
                    calculation_version=_CALCULATION_VERSION,
                ),
                mission_id=ctx.deps.mission_id,
            )
        )
        artifact_ids.append(stored.id)

    if not artifact_ids:
        return _refuse(
            f"no treatment variant of {experiment_id} had stamped evidence",
            warning=policy.WARN_MISSING_VARIANT_EVIDENCE,
        )

    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=(
            f"{experiment_id}: {len(artifact_ids)} treatment(s) evaluated against control "
            f"{control.name}"
        ),
        provenance=ArtifactProvenance(
            evidence_ids=list(evidence_ids), calculation_version=_CALCULATION_VERSION
        ),
        warnings=warnings,
    )

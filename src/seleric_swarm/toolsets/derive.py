"""DeriveToolset — typed arithmetic over recorded evidence (M2.3).

The agent-loop entry to :mod:`seleric_swarm.derive`. It resolves its inputs from
the mission's ``ArtifactStore`` (a ``derivation`` from an earlier call, or a
``result_set``), runs one op, and persists the result as an
``artifact_type="derivation"`` artifact citing every input's evidence. A refusal
is returned as a failed ``ToolResult`` carrying the op's own error code, never
raised.

Rules 4/5 as everywhere else: no fetching, no calling another tool.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.analytics.grain import CALCULATION_VERSION
from seleric_swarm.contracts.pipeline import Derivation, ResultSet
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.derive import derive as run_derive
from seleric_swarm.semantic import SemanticReadModel

DeriveInput = ResultSet | Derivation

_READABLE: dict[str, type[Derivation | ResultSet]] = {
    "derivation": Derivation,
    "result_set": ResultSet,
}


def _read_artifact(artifact: Artifact) -> DeriveInput:
    model = _READABLE.get(artifact.artifact_type)
    if model is None:
        raise ValueError(
            f"artifact {artifact.id!r} is {artifact.artifact_type!r}; "
            "derive reads 'derivation' or 'result_set' artifacts"
        )
    return model.model_validate(artifact.payload)


def _evidence(result: Derivation, sources: list[Artifact]) -> list[str]:
    cited = [evidence_id for artifact in sources for evidence_id in artifact.evidence_ids]
    cited.extend(row.evidence_id for row in result.rows)
    return list(dict.fromkeys(cited))


def _summary(result: Derivation) -> str:
    value = "n/a" if result.value is None else f"{result.value:.6g}"
    unit = f" {result.unit}" if result.unit else ""
    return f"derive {result.op}: {value}{unit} over {len(result.inputs)} input(s)"


async def derive(
    ctx: RunContext[SelericDeps],
    op: str,
    inputs: list[str],
    params: dict[str, Any] | None = None,
) -> ToolResult:
    """Run one typed derivation op over recorded evidence.

    ``inputs`` are artifact ids: a ``derivation`` artifact produced by an earlier
    call, or a ``result_set``. The new derivation is persisted and its id
    returned. A catalogue disagreement (units, windows, additivity, composition,
    join keys) returns ``success=False`` with the op's error code.
    """
    sources: list[Artifact] = []
    resolved: list[DeriveInput] = []
    for artifact_id in inputs:
        artifact = ctx.deps.artifact_store.get(artifact_id)
        if artifact is None:
            return ToolResult(
                success=False,
                summary=f"unknown artifact {artifact_id!r}",
                error_code="DERIVE_UNKNOWN_INPUT",
                retryable=False,
            )
        try:
            resolved.append(_read_artifact(artifact))
        except ValueError as exc:
            return ToolResult(
                success=False,
                summary=str(exc),
                error_code="DERIVE_INPUT_UNSUPPORTED",
                retryable=False,
            )
        sources.append(artifact)

    read_model = SemanticReadModel.from_legacy(ctx.deps.catalogue)
    result = run_derive(op, resolved, params or {}, read_model=read_model)
    if result.error is not None:
        return ToolResult(
            success=False,
            summary=f"derive {result.op} refused: {result.error.message}",
            error_code=result.error.code,
            retryable=False,
        )

    evidence_ids = _evidence(result, sources)
    if not evidence_ids:
        return ToolResult(
            success=False,
            summary="derive produced no evidence to cite",
            error_code="DERIVE_NO_EVIDENCE",
            retryable=False,
        )
    artifact = ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type="derivation",
            payload=result.model_dump(mode="json"),
            classification="derived",
            evidence_ids=evidence_ids,
            provenance=ArtifactProvenance(
                evidence_ids=evidence_ids,
                calculation_version=CALCULATION_VERSION,
            ),
            mission_id=ctx.deps.mission_id,
        )
    )
    return ToolResult(
        success=True,
        artifact_ids=[artifact.id],
        summary=_summary(result),
        provenance=ArtifactProvenance(evidence_ids=evidence_ids),
    )

"""Derivation engine (M2.3): one ``Derivation`` per arithmetic op.

The engine never raises on catalogue disagreement: an op that refuses returns a
``Derivation`` whose ``error`` is a ``StageError`` (code ``derive``), so one bad
step does not end a mission. Only an unknown op name raises, because the op set
is fixed by the plan.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from seleric_swarm.contracts.pipeline import Derivation, JsonValue, Ref, StageError
from seleric_swarm.derive._ops import OPS, DeriveInput, Refusal
from seleric_swarm.semantic import SemanticReadModel

__all__ = ["derive"]


def derive(
    op: str,
    inputs: Sequence[DeriveInput],
    params: Mapping[str, JsonValue] | None = None,
    *,
    read_model: SemanticReadModel,
    ref: Ref | None = None,
) -> Derivation:
    operation = OPS.get(op)
    if operation is None:
        known = ", ".join(sorted(OPS))
        raise ValueError(f"unknown derive op {op!r}; known ops: {known}")
    resolved = list(inputs)
    merged_params: dict[str, JsonValue] = dict(params or {})
    result_ref = ref or Ref(kind="derivation", id=_derivation_id(op, resolved, merged_params))
    try:
        outcome = operation(resolved, merged_params, read_model)
    except Refusal as refusal:
        return Derivation(
            ref=result_ref,
            op=op,
            inputs=tuple(item.ref for item in resolved),
            params=merged_params,
            value=None,
            unit=None,
            error=StageError(stage="derive", code=refusal.code, message=refusal.message),
        )
    if outcome.params:
        merged_params.update(dict(outcome.params))
    return Derivation(
        ref=result_ref,
        op=op,
        inputs=tuple(item.ref for item in resolved),
        params=merged_params,
        value=outcome.value,
        unit=outcome.unit,
        rows=outcome.rows,
    )


def _derivation_id(op: str, inputs: Sequence[DeriveInput], params: Mapping[str, JsonValue]) -> str:
    payload = {
        "op": op,
        "inputs": [f"{item.ref.kind}:{item.ref.id}" for item in inputs],
        "params": params,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return f"derive-{op}-{digest}"

"""What each stage found, as plain JSON-able data (plan P2.4): the expected side of a fixture test and the digest a
trace event carries. Only what the stage decided is in it; the input it was given is not repeated."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from seleric_swarm.diagnosis.domain.errors import InvalidRequest
from seleric_swarm.diagnosis.operators.pipeline import STAGES, StageResults


def summarise(stage: str, results: StageResults) -> dict[str, Any]:
    if stage not in STAGES:
        raise InvalidRequest(f"no stage {stage!r}; the stages are {list(STAGES)}")
    if stage == "is_real":
        assert results.event is not None, "the stage has not run"
        if results.event.analysis is None:
            return {"early": asdict(results.event.early) if results.event.early is not None else None}
        ana = results.event.analysis
        return {
            "event": asdict(ana.event), "identities": [i.describe() for i in ana.identities], "quality": list(ana.quality),
            "assumptions": list(ana.assumptions), "reference_kind": ana.ref_kind, "delta": ana.delta,
            "unusual": ana.unusual, "notable": ana.notable,
        }
    if stage == "what":
        assert results.what is not None, "the stage has not run"
        return asdict(results.what)
    if stage == "where":
        assert results.dims is not None, "the stage has not run"
        return {"dimensions": [asdict(d) for d in results.dims]}
    assert results.why is not None, "the stage has not run"
    return asdict(results.why)

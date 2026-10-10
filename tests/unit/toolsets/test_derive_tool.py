"""derive tool wiring: input resolution + artifact persistence (M2.3)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from seleric_swarm.contracts.pipeline import Ref, ResultRow, ResultSet, WindowSpec
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import derive as tool


def _result_set_artifact(
    artifact_id: str, value: float, evidence_id: str, *, metric_id: str = "net_sales"
) -> Artifact:
    result_set = ResultSet(
        ref=Ref(kind="result_set", id=f"rs-{artifact_id}"),
        step_id="s1",
        metric_id=metric_id,
        unit="USD",
        window=WindowSpec(role="event", start=date(2026, 1, 1), end=date(2026, 1, 31)),
        breakdowns=(),
        filters=(),
        rows=(ResultRow(evidence_id=evidence_id, dimensions={}, value=value),),
    )
    return Artifact(
        id=artifact_id,
        workspace_id="w",
        artifact_type="result_set",
        payload=result_set.model_dump(mode="json"),
        classification="derived",
        evidence_ids=[evidence_id],
        provenance=ArtifactProvenance(evidence_ids=[evidence_id], calculation_version="calc-v1"),
        mission_id="m",
    )


def _ctx(store: InMemoryArtifactStore) -> SimpleNamespace:
    deps = SimpleNamespace(
        artifact_store=store,
        catalogue=CatalogueSnapshot(),
        principal=SimpleNamespace(workspace_id="w"),
        mission_id="m",
    )
    return SimpleNamespace(deps=deps)


@pytest.mark.asyncio
async def test_derive_persists_a_derivation_artifact():
    store = InMemoryArtifactStore()
    store.put(_result_set_artifact("a", 100.0, "ev-a"))
    store.put(_result_set_artifact("b", 130.0, "ev-b"))

    res = await tool.derive(_ctx(store), "delta", ["a", "b"])

    assert res.success
    assert len(res.artifact_ids) == 1
    stored = store.get(res.artifact_ids[0])
    assert stored is not None
    assert stored.artifact_type == "derivation"
    assert stored.classification == "derived"
    assert stored.payload["op"] == "delta"
    assert stored.payload["value"] == 30.0
    assert set(stored.evidence_ids) >= {"ev-a", "ev-b"}


@pytest.mark.asyncio
async def test_derive_chains_from_a_prior_derivation():
    store = InMemoryArtifactStore()
    store.put(_result_set_artifact("a", 100.0, "ev-a"))
    store.put(_result_set_artifact("b", 130.0, "ev-b"))

    first = await tool.derive(_ctx(store), "delta", ["a", "b"])
    second = await tool.derive(_ctx(store), "share", [first.artifact_ids[0], "a"])

    assert second.success
    chained = store.get(second.artifact_ids[0])
    assert chained is not None
    assert chained.payload["op"] == "share"
    assert chained.payload["value"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_derive_returns_a_refusal_as_a_failed_result():
    store = InMemoryArtifactStore()
    store.put(_result_set_artifact("a", 1.0, "ev-a"))
    store.put(_result_set_artifact("b", 0.0, "ev-b", metric_id="orders"))

    res = await tool.derive(_ctx(store), "ratio", ["a", "b"])

    assert not res.success
    assert res.error_code == "zero_denominator"
    assert res.artifact_ids == []


@pytest.mark.asyncio
async def test_derive_rejects_an_unknown_input():
    res = await tool.derive(_ctx(InMemoryArtifactStore()), "delta", ["missing"])

    assert not res.success
    assert res.error_code == "DERIVE_UNKNOWN_INPUT"


@pytest.mark.asyncio
async def test_derive_rejects_a_non_derivable_artifact_type():
    store = InMemoryArtifactStore()
    store.put(
        Artifact(
            id="ev1",
            workspace_id="w",
            artifact_type="evidence",
            payload={},
            classification="factual",
            evidence_ids=["ev-1"],
            provenance=ArtifactProvenance(evidence_ids=["ev-1"]),
            mission_id="m",
        )
    )

    res = await tool.derive(_ctx(store), "sum", ["ev1"])

    assert not res.success
    assert res.error_code == "DERIVE_INPUT_UNSUPPORTED"

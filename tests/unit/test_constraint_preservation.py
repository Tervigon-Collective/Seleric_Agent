"""Constraint preservation — the "Pro Suspender Boots by source, exclude
exchanges" trace: the agent dropped the product/exchange constraints, ran a
bare `by source` query, and shipped it `completed`.

Two guards, one feature:

* A1 (`toolsets/semantic.py`): a Cube "unknown dimension" error on a
  user-supplied (non-brand) filter must surface as ``UNSUPPORTED_QUERY`` — NOT
  trigger a silent strip-and-retry that answers a different question. Only an
  unresolved *brand* is safe to drop.
* A3 (`agent/scope.py` + `validation/signals.py`): a requested breakdown that
  resolved to a real dimension but is absent from the answer's evidence forces
  REVISE, never a silent PASS/``completed``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import semantic

_MISSION = "MS3-constraint"


# --- A1: the constraint-erasing fallback ------------------------------------


class _FilterAwareMcp:
    """Errors on any query carrying a filter on a `bad` dimension; otherwise
    returns one row. Lets a test assert error-then-(no)retry by inspecting the
    filters each call received."""

    def __init__(
        self, *, bad_dim: str, error: str, metric_id: str, bad_value: str | None = None
    ) -> None:
        self.bad_dim = bad_dim
        self.error = error
        self.metric_id = metric_id
        # When set, only THIS value of bad_dim is rejected (e.g. brand 999 is
        # unknown but the injected default brand 20 is valid). When None, any
        # filter on bad_dim errors.
        self.bad_value = bad_value
        self.metric_calls: list[list[dict[str, Any]]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        if capability != "seleric.metrics_query":
            return {}
        filters = list(arguments.get("filters") or [])
        self.metric_calls.append(filters)
        bad = [
            f
            for f in filters
            if f.get("dimension") == self.bad_dim
            and (self.bad_value is None or self.bad_value in (f.get("values") or []))
        ]
        if bad:
            return {"error": self.error, "rows": [], "provenance": {}}
        return {"rows": [{self.metric_id: 42}], "provenance": {"query_id": "q1", "currency": "INR"}}


class _Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _semantic_deps(mcp: Any) -> SelericDeps:
    return SelericDeps(
        mission_id=_MISSION,
        as_of=datetime(2026, 9, 24, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )


@pytest.mark.asyncio
async def test_unknown_nonbrand_dimension_is_unsupported_not_stripped():
    # Cube rejects a product filter the chosen metric can't carry (the trace).
    mcp = _FilterAwareMcp(
        bad_dim="product_title",
        error="Unknown dimension: product_title",
        metric_id="attributed_orders",
    )
    result = await semantic.query_metrics(
        _Ctx(_semantic_deps(mcp)),
        metric_id="attributed_orders",
        dimensions={"product_title": "Pro Suspender Boots"},
    )
    assert result.success is False
    assert result.error_code == "UNSUPPORTED_QUERY"
    # The load-bearing assertion: no silent strip-and-retry. Exactly one query
    # ran, and it still carried the product filter.
    assert len(mcp.metric_calls) == 1
    assert any(f["dimension"] == "product_title" for f in mcp.metric_calls[0])


@pytest.mark.asyncio
async def test_unknown_brand_is_dropped_but_other_filters_survive():
    # An unresolved brand is the one thing safe to drop — and only the brand.
    mcp = _FilterAwareMcp(
        bad_dim="brand_id",
        error="Unknown brand: 999",
        metric_id="product_orders",
        bad_value="999",  # the default brand 20 the retry falls back to is valid
    )
    result = await semantic.query_metrics(
        _Ctx(_semantic_deps(mcp)),
        metric_id="product_orders",
        dimensions={"brand_id": "999", "product_title": "Pro Suspender Boots"},
    )
    assert result.success is True
    assert len(mcp.metric_calls) == 2  # errored, retried
    # Retry dropped the bad brand and fell back to the default brand (20); the
    # product filter was preserved.
    retry = {f["dimension"]: f["values"] for f in mcp.metric_calls[1]}
    assert retry.get("brand_id") == ["20"]
    assert "product_title" in retry


# --- A3: requested-breakdown coverage ---------------------------------------

_ALIASES = {"source": "source_name", "order source": "source_name", "vendor": "vendor"}
_DIMS = frozenset({"source_name", "vendor", "lt_platform"})




def _evidence_artifact(
    store: InMemoryArtifactStore, *, day: int, dimensions: dict[str, str], grain: str = "day"
) -> None:
    stamp = datetime(2026, 9, day, tzinfo=UTC)
    payload = EvidenceArtifact(
        metric_id="attributed_orders",
        dimensions=dimensions,
        grain=grain,  # type: ignore[arg-type]
        as_of=datetime.now(UTC),
        period_start=stamp,
        period_end=stamp,
        value=100.0 + day,
        source_query={"measure": "attributed_orders"},
    )
    store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:attributed_orders:{day}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    )






def _filtered_evidence(store: InMemoryArtifactStore, *, day: int, dimension: str, value: str) -> None:
    """Evidence with an applied Cube filter (not a grouping) — how a named value
    or workspace brand scope actually lands: in provenance, dimensions stay {}."""
    stamp = datetime(2026, 9, day, tzinfo=UTC)
    payload = EvidenceArtifact(
        metric_id="total_ad_spend",
        dimensions={},
        grain="month",
        as_of=datetime.now(UTC),
        period_start=stamp,
        period_end=stamp,
        value=1000.0 + day,
        source_query={"measure": "total_ad_spend"},
    )
    store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:total_ad_spend:{day}"],
            provenance=ArtifactProvenance(
                query_version="q1",
                source_metadata={"filters_applied": [{"dimension": dimension, "operator": "equals", "values": [value]}]},
            ),
            mission_id=_MISSION,
        )
    )












def _catalogue(dim_to_metrics: dict[str, list[str]]):
    """Minimal snapshot where each metric lists the dims that map to it."""
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot

    metric_dims: dict[str, set[str]] = {}
    for dim, mids in dim_to_metrics.items():
        for mid in mids:
            metric_dims.setdefault(mid, set()).add(dim)
    metrics = tuple(
        CatalogueMetricMeta(id=mid, supported_dimensions=sorted(dims))
        for mid, dims in metric_dims.items()
    )
    return CatalogueSnapshot(metrics=metrics, dimensions=tuple(dim_to_metrics))
















def _metric_evidence(store: InMemoryArtifactStore, metric_id: str, dimensions: dict[str, str]) -> str:
    stamp = datetime(2026, 9, 28, tzinfo=UTC)
    payload = EvidenceArtifact(
        metric_id=metric_id,
        dimensions=dimensions,
        grain="none",  # type: ignore[arg-type]
        as_of=datetime.now(UTC),
        period_start=stamp,
        period_end=stamp,
        value=1.0,
        source_query={"measure": metric_id},
    )
    artifact = Artifact(
        workspace_id="ws1",
        artifact_type="evidence",
        payload=payload.model_dump(mode="json"),
        classification="factual",
        evidence_ids=[f"raw:{metric_id}"],
        provenance=ArtifactProvenance(query_version="q1"),
        mission_id=_MISSION,
    )
    store.put(artifact)
    return artifact.id





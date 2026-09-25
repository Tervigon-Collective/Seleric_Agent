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
from seleric_swarm.agent.scope import RequiredScope, build_required_scope
from seleric_swarm.agent.validation import EvidenceValidator
from seleric_swarm.agent.validation.signals import check_scope_coverage
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


def test_build_required_scope_extracts_resolvable_breakdown():
    scope = build_required_scope(
        "orders by source last 5 days", alias_index=_ALIASES, dimension_ids=_DIMS
    )
    assert scope.breakdowns == frozenset({frozenset({"source_name"})})


def _evidence_artifact(store: InMemoryArtifactStore, *, day: int, dimensions: dict[str, str]) -> None:
    stamp = datetime(2026, 9, day, tzinfo=UTC)
    payload = EvidenceArtifact(
        metric_id="attributed_orders",
        dimensions=dimensions,
        grain="day",
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


def test_scope_coverage_not_applicable_without_breakdown():
    store = InMemoryArtifactStore()
    _evidence_artifact(store, day=1, dimensions={})
    out = check_scope_coverage(store.list_for_mission(_MISSION), RequiredScope())
    assert out.status == "NOT_APPLICABLE"
    assert not out.gaps


def test_scope_coverage_missing_breakdown_blocks():
    store = InMemoryArtifactStore()
    for d in range(1, 6):  # bare aggregate — no source_name grouping (the trace)
        _evidence_artifact(store, day=d, dimensions={})
    out = check_scope_coverage(
        store.list_for_mission(_MISSION), RequiredScope(breakdowns=frozenset({"source_name"}))
    )
    assert out.status == "INSUFFICIENT"
    assert out.gaps and out.gaps[0].blocking


def test_scope_coverage_satisfied_when_grouped():
    store = InMemoryArtifactStore()
    for d in range(1, 6):
        _evidence_artifact(store, day=d, dimensions={"source_name": "web"})
    out = check_scope_coverage(
        store.list_for_mission(_MISSION), RequiredScope(breakdowns=frozenset({"source_name"}))
    )
    assert out.status == "OK"
    assert not out.gaps


def test_ambiguous_breakdown_sibling_satisfies_coverage():
    # L4 regression: "by channel" resolves to a candidate set {channel,
    # lt_channel}; grouping by the sibling lt_channel must satisfy coverage,
    # not force a REVISE loop.
    store = InMemoryArtifactStore()
    for d in range(1, 6):
        _evidence_artifact(store, day=d, dimensions={"lt_channel": "ig_feed"})
    scope = RequiredScope(breakdowns=frozenset({frozenset({"channel", "lt_channel"})}))
    out = check_scope_coverage(store.list_for_mission(_MISSION), scope)
    assert out.status == "OK"
    assert not out.gaps


def test_ambiguous_breakdown_requires_at_least_one_candidate():
    # Grouping by nothing still fails, even with an ambiguous candidate set.
    store = InMemoryArtifactStore()
    for d in range(1, 6):
        _evidence_artifact(store, day=d, dimensions={})
    scope = RequiredScope(breakdowns=frozenset({frozenset({"channel", "lt_channel"})}))
    out = check_scope_coverage(store.list_for_mission(_MISSION), scope)
    assert out.status == "INSUFFICIENT"
    assert out.gaps and out.gaps[0].blocking


def _validation_deps(store: InMemoryArtifactStore, scope: RequiredScope) -> SelericDeps:
    return SelericDeps(
        mission_id=_MISSION,
        as_of=datetime.now(UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=None,  # not touched by the sync checks
        artifact_store=store,
        limits=ExecutionLimits(),
        required_scope=scope,
    )


def test_missing_breakdown_forces_revise_end_to_end():
    store = InMemoryArtifactStore()
    for d in range(1, 11):  # clean series so only the missing breakdown drives the verdict
        _evidence_artifact(store, day=d, dimensions={})
    deps = _validation_deps(store, RequiredScope(breakdowns=frozenset({"source_name"})))
    outcome = EvidenceValidator().score(deps)
    assert outcome.verdict == "REVISE"
    assert outcome.ok is False


def test_grouped_breakdown_does_not_trip_verdict():
    store = InMemoryArtifactStore()
    for d in range(1, 11):
        _evidence_artifact(store, day=d, dimensions={"source_name": "web"})
    deps = _validation_deps(store, RequiredScope(breakdowns=frozenset({"source_name"})))
    outcome = EvidenceValidator().score(deps)
    # scope coverage is satisfied → it contributes no REVISE reason.
    assert "breakdown" not in (outcome.reason or "")

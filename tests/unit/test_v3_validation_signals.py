"""Two-signal validator tests — docs/BUG_SHEET.md #12 ported forward (Sprint 3, C).

#12 is filed as **"INVESTIGATED, NOT A BUG"**: ``trust_label: STRONG`` alongside
``verdict: REVISE`` is intentional, coherent design — "the evidence we have is
solid, but there's an unresolved competing explanation we haven't ruled out
yet". ``03_PROFILE_CAPABILITIES.md`` §9 criterion (c)5 makes reproducing that
structure an exit criterion for this profile, because the obvious way to port
two functions is to collapse them into one score, which would destroy it.

Why the combination is *possible* at all, in numbers: the STRONG floor is 0.72
and ``revise_below`` is 0.55 (`toolsets/policy_config.py`, copied from
`config/skeptic_policies.yaml`). Anything clearing STRONG clears the verdict's
trust gate with room to spare, so a REVISE on a STRONG claim can only come from
a gap, an alternative, or a warning — never from the trust score. These tests
pin each of those three routes separately.

Offline: no `runtime` fixture, no MCP, no LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import policy_config as policy

_MISSION = "MS3-signals"


def _deps(store: InMemoryArtifactStore) -> SelericDeps:
    return SelericDeps(
        mission_id=_MISSION,
        as_of=datetime.now(UTC),
        principal=Principal(
            principal_id="p1",
            workspace_id="ws-1",
            user_id="user-1",
            authenticated=False,
            auth_method=PrincipalAuthMethod.ANONYMOUS,
        ),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=store,
        limits=ExecutionLimits(),
    )


def _evidence(
    store: InMemoryArtifactStore,
    *,
    day: int,
    value: float | None = 100.0,
    metric: str = "metric.net_sales",
) -> str:
    stamp = datetime(2026, 9, day, tzinfo=UTC)
    payload = EvidenceArtifact(
        metric_id=metric,
        grain="day",
        as_of=datetime.now(UTC),
        period_start=stamp,
        period_end=stamp,
        value=value,
        source_query={"measure": metric},
    )
    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{metric}:{day}:{value}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    ).id


def _clean_series(store: InMemoryArtifactStore, *, count: int = 10) -> list[str]:
    """A series with no quality problems of its own, so any REVISE in a test
    below is unambiguously caused by the thing that test is about."""
    return [_evidence(store, day=d, value=100.0 + d) for d in range(1, count + 1)]


def _derived(
    store: InMemoryArtifactStore, artifact_type: str, payload: dict[str, Any], evidence_ids: list[str]
) -> str:
    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type=artifact_type,
            payload=payload,
            classification="derived",
            evidence_ids=evidence_ids,
            provenance=ArtifactProvenance(
                evidence_ids=evidence_ids, calculation_version=f"{artifact_type}.v0"
            ),
            mission_id=_MISSION,
        )
    ).id


# ---- the headline: bug #12's exact shape, all three routes -------------------


# 0.8 lands inside the STRONG band [0.72, 0.9) once the default profile
# renormalizes onto evidence_quality alone (its only feeding dimension here).
# A perfect 1.0 would score VERIFIED, which is a *different* label and would not
# reproduce #12's reported shape.
_STRONG_BAND_SIGNAL = 0.8










def test_thresholds_leave_room_for_strong_plus_revise():
    """The mechanism itself. If STRONG ever dropped below revise_below, every
    STRONG claim would auto-PASS the trust gate for a different reason and #12's
    shape would silently become unreachable."""
    assert policy.TRUST_LABEL_THRESHOLDS["STRONG"] > policy.TRUST_REVISE_BELOW


# ---- verdict precedence and the REJECT path ---------------------------------










# ---- ported trust arithmetic ------------------------------------------------










# ---- V3-native checks -------------------------------------------------------


















# -- requested time windows -----------------------------------------------------
#
# Live 2026-10-06 (MS3-167d9f4838): the question compared "the last 3 days versus
# today"; the resolver dropped the second window, nothing reconciled the periods,
# and the mission shipped the 3-day half as `completed`. `RequiredScope` had no
# field to hold a second window, so the gate below is what closes it.


def _span(store: InMemoryArtifactStore, *, start: int, end: int, grain: str = "none") -> str:
    payload = EvidenceArtifact(
        metric_id="metric.net_sales",
        grain=grain,  # type: ignore[arg-type]
        as_of=datetime.now(UTC),
        period_start=datetime(2026, 10, start, tzinfo=UTC),
        period_end=datetime(2026, 10, end, tzinfo=UTC),
        value=100.0,
        source_query={"measure": "metric.net_sales"},
    )
    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{start}:{end}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    ).id











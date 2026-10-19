"""Unit + regression tests for toolsets/analytics.py (Sprint 1, Profile C).

The headline regression is ``docs/BUG_SHEET.md`` #14, ported forward in
**both** halves per ``03_PROFILE_CAPABILITIES.md`` §9 exit criterion (a)2:

1. per-day evidence reaches the detector un-normalized, and
2. a grain-mismatched evidence set is *rejected*, not normalized.

Half 2 is the new one. In swarm_v2 the guarantee came from a typed
``granularity`` threaded through mission state so the fetcher and the
detector could not disagree. V3's rules 4+5 delete that thread — the LLM
picks the grain per call — so the guarantee has to live in the tool as a
precondition, or #14 returns as an intermittent instead of a test failure.

Fake-context pattern mirrors ``tests/unit/test_semantic_toolset.py`` (Profile
B) so the two toolsets' tests stay readable side by side.
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
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(store: InMemoryArtifactStore | None = None) -> SelericDeps:
    return SelericDeps(
        mission_id="mission-1",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),  # analytics tools never touch MCP — rule 5
        artifact_store=store or InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )


def _put_evidence(
    store: InMemoryArtifactStore,
    *,
    metric: str = "metric.net_sales",
    grain: str = "day",
    start: str,
    end: str,
    value: float | None,
    dimensions: dict[str, str] | None = None,
) -> str:
    evidence = EvidenceArtifact(
        metric_id=metric,
        dimensions=dimensions or {},
        grain=grain,  # type: ignore[arg-type]
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        period_start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        period_end=datetime.fromisoformat(end).replace(tzinfo=UTC),
        value=value,
        source_query={"measure": metric},
    )
    artifact = store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="evidence",
            payload=evidence.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{metric}:{start}:{end}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id="mission-1",
        )
    )
    return artifact.id


def _daily(store: InMemoryArtifactStore, values: dict[str, float], **kw: Any) -> list[str]:
    return [_put_evidence(store, start=day, end=day, value=value, **kw) for day, value in values.items()]


def _findings(store: InMemoryArtifactStore, result: Any) -> list[dict[str, Any]]:
    return [store.get(aid).payload for aid in result.artifact_ids]


# ---- docs/BUG_SHEET.md #14, half 1: per-day evidence is not normalized -------------




# ---- docs/BUG_SHEET.md #14, half 2: a mismatched set is refused, never normalized ---








# ---- detect_anomalies: remaining behavior ------------------------------------------












# ---- compare_periods -----------------------------------------------------------------










# ---- evidence loading / provenance ---------------------------------------------------










# ---- merge companion metric breakdowns --------------------------------------


def _breakdown_row(
    store: InMemoryArtifactStore,
    *,
    metric: str,
    campaign: str,
    value: float,
    day: str = "2026-09-16",
) -> str:
    return _put_evidence(
        store,
        metric=metric,
        grain="none",
        start=day,
        end=day,
        value=value,
        dimensions={"campaign_name": campaign},
    )








# ---- merge a multi-metric time series ---------------------------------------








def finding_store_line(summary: str, day: str, column_marker: str) -> str:
    lines = summary.splitlines()
    header = next(line for line in lines if line.startswith("| date"))
    col = header.split("|").index(f" {column_marker} ")
    row = next(line for line in lines if line.startswith(f"| {day}"))
    return row.split("|")[col].strip()



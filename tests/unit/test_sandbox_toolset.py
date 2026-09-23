"""Unit tests for toolsets/sandbox.py — the in-process python sandbox.

Covers the failure paths, not just the happy one: code error / syntax error /
blocked import / no-output all raise ModelRetry (the model's recovery signal);
timeout and evidence-miss return structured refusals. The NullMcpClient deps
prove the sandbox never fetches (non-negotiable rule 5).

Fake-context pattern mirrors tests/unit/test_analytics_toolset.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai import ModelRetry

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import sandbox


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(store: InMemoryArtifactStore, mission_id: str = "mission-sbx") -> SelericDeps:
    return SelericDeps(
        mission_id=mission_id,
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),  # sandbox never fetches — rule 5
        artifact_store=store,
        limits=ExecutionLimits(),
    )


def _put_evidence(store: InMemoryArtifactStore, *, metric: str = "net_sales", value: float = 100.0) -> str:
    evidence = EvidenceArtifact(
        metric_id=metric,
        grain="day",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        period_start=datetime(2026, 9, 18, tzinfo=UTC),
        period_end=datetime(2026, 9, 18, tzinfo=UTC),
        value=value,
        unit="INR",
        source_query={"measure": metric},
    )
    return store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="evidence",
            payload=evidence.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{metric}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id="mission-sbx",
        )
    ).id


@pytest.mark.asyncio
async def test_happy_path_writes_finding_with_numeric_metrics() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store, value=100.0)
    e2 = _put_evidence(store, value=50.0)
    ctx = FakeRunContext(_deps(store))
    result = await sandbox.run_python(
        ctx,
        "result = {'total': sum(e['value'] for e in evidence)}",
        [eid, e2],
        purpose="sum values",
    )
    assert result.success is True
    assert result.artifact_ids
    finding = store.get(result.artifact_ids[0]).payload
    assert finding["finding_type"] == "sandbox_computation"
    assert finding["metrics"]["total"] == 150.0
    assert set(finding["evidence_ids"]) == {eid, e2}


@pytest.mark.asyncio
async def test_code_error_raises_model_retry() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    with pytest.raises(ModelRetry) as exc:
        await sandbox.run_python(ctx, "result = 1 / 0", [eid])
    assert "ZeroDivisionError" in str(exc.value)


@pytest.mark.asyncio
async def test_syntax_error_raises_model_retry() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    with pytest.raises(ModelRetry) as exc:
        await sandbox.run_python(ctx, "result = (", [eid])
    assert "syntax" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_blocked_import_raises_model_retry() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    with pytest.raises(ModelRetry) as exc:
        await sandbox.run_python(ctx, "import socket", [eid])
    assert "socket" in str(exc.value)


@pytest.mark.asyncio
async def test_no_output_raises_model_retry() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    with pytest.raises(ModelRetry):
        await sandbox.run_python(ctx, "x = 1 + 1", [eid])


@pytest.mark.asyncio
async def test_evidence_miss_refuses() -> None:
    store = InMemoryArtifactStore()
    ctx = FakeRunContext(_deps(store))
    result = await sandbox.run_python(ctx, "result = 1", ["does-not-exist"])
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"


@pytest.mark.asyncio
async def test_empty_evidence_ids_refuses() -> None:
    store = InMemoryArtifactStore()
    ctx = FakeRunContext(_deps(store))
    result = await sandbox.run_python(ctx, "result = 1", [])
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"


@pytest.mark.asyncio
async def test_timeout_returns_refusal(monkeypatch) -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    monkeypatch.setenv("SELERIC_SANDBOX_TIMEOUT_S", "0.3")
    # Sleep past the 0.3s budget; the abandoned daemon thread just sleeps out
    # (no CPU burn) and dies at process exit — the accepted in-process ceiling.
    result = await sandbox.run_python(ctx, "import time\ntime.sleep(3)\nresult = 1", [eid])
    assert result.success is False
    assert result.error_code == "SANDBOX_TIMEOUT"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_disabled_by_env(monkeypatch) -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    monkeypatch.setenv("SELERIC_SANDBOX_ENABLED", "0")
    result = await sandbox.run_python(ctx, "result = 1", [eid])
    assert result.success is False
    assert result.error_code == "SANDBOX_DISABLED"


@pytest.mark.asyncio
async def test_writes_files_to_workdir(tmp_path, monkeypatch) -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store, mission_id="mission-files"))
    monkeypatch.setattr(sandbox, "repo_root", lambda: tmp_path)
    result = await sandbox.run_python(
        ctx,
        "import os\n"
        "open(os.path.join(WORKDIR, 'out.txt'), 'w').write('hi')\n"
        "result = 'wrote'",
        [eid],
    )
    assert result.success is True
    assert (tmp_path / ".data" / "sandbox" / "mission-files" / "out.txt").read_text() == "hi"
    assert "out.txt" in result.summary  # filenames are listed in the finding statement
    assert any("1 file" in w for w in result.warnings)

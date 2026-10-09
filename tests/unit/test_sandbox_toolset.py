"""Unit tests for toolsets/sandbox.py — the in-process python sandbox.

Covers the failure paths, not just the happy one: code error / syntax error /
blocked import / no-output return a failed, retryable result (the model's recovery
signal — never ModelRetry, whose exhaustion fails the whole attempt);
timeout and evidence-miss return structured refusals. The NullMcpClient deps
prove the sandbox never fetches (non-negotiable rule 5).

Fake-context pattern mirrors tests/unit/test_analytics_toolset.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
async def test_code_error_returns_a_retryable_failure() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    res = await sandbox.run_python(ctx, 'result = 1 / 0', [eid])
    assert not res.success and res.retryable
    assert "ZeroDivisionError" in res.summary


@pytest.mark.asyncio
async def test_syntax_error_returns_a_retryable_failure() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    res = await sandbox.run_python(ctx, 'result = (', [eid])
    assert not res.success and res.retryable
    assert "syntax" in res.summary.lower()


@pytest.mark.asyncio
async def test_blocked_import_returns_a_retryable_failure() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    res = await sandbox.run_python(ctx, 'import socket', [eid])
    assert not res.success and res.retryable
    assert "socket" in res.summary


@pytest.mark.asyncio
async def test_no_output_returns_a_retryable_failure() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store))
    res = await sandbox.run_python(ctx, 'x = 1 + 1', [eid])
    assert not res.success and res.retryable


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
async def test_result_is_not_truncated_and_nested_numbers_are_recorded(tmp_path, monkeypatch) -> None:
    """Live failure: a two-period ranking came back cut at 400 chars, so the
    second period vanished and the answer said "no data" for it."""
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store, mission_id="mission-full"))
    monkeypatch.setattr(sandbox, "repo_root", lambda: tmp_path)
    code = (
        "result = {p: [{'name': 'item-%d-with-a-long-descriptive-label' % i, 'revenue': 1000 + i}"
        " for i in range(15)] for p in ('first', 'second')}"
    )
    result = await sandbox.run_python(ctx, code, [eid])
    assert result.success is True
    assert "item-14-with-a-long-descriptive-label" in result.summary
    assert '"second"' in result.summary
    metrics = store.get(result.artifact_ids[0]).payload["metrics"]
    assert metrics["second[14].revenue"] == 1014.0
    assert metrics["first[0].revenue"] == 1000.0


@pytest.mark.asyncio
async def test_oversized_result_spills_to_workdir(tmp_path, monkeypatch) -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store)
    ctx = FakeRunContext(_deps(store, mission_id="mission-spill"))
    monkeypatch.setattr(sandbox, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("SELERIC_SANDBOX_RESULT_CHARS", "50")
    result = await sandbox.run_python(ctx, "result = list(range(500))", [eid])
    assert result.success is True
    spill = tmp_path / ".data" / "sandbox" / "mission-spill" / "result_1.json"
    assert spill.exists()
    assert "499" in spill.read_text(encoding="utf-8")
    assert "result_1.json" in result.summary
    assert any("result_1.json" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_scalar_result_is_recorded_as_metric() -> None:
    store = InMemoryArtifactStore()
    eid = _put_evidence(store, value=7.0)
    ctx = FakeRunContext(_deps(store))
    result = await sandbox.run_python(ctx, "result = evidence[0]['value'] * 2", [eid])
    assert store.get(result.artifact_ids[0]).payload["metrics"] == {"result": 14.0}


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

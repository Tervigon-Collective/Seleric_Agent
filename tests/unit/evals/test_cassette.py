from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from seleric_swarm.evals import (
    Cassette,
    CassetteMiss,
    RecordingMcpClient,
    ReplayMcpClient,
)


class _Inner:
    capabilities: ClassVar[set[str]] = {
        "seleric.metrics_query",
        "seleric.catalogue_list_metrics",
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, object]):
        self.calls.append((capability, arguments))
        return {"rows": [{"value": 5.0}], "capability": capability, "echo": arguments}


async def test_record_then_replay_roundtrip(tmp_path: Path) -> None:
    recorder = RecordingMcpClient(_Inner())
    await recorder.call(
        agent_id="v3_agent",
        capability="seleric.metrics_query",
        arguments={"metric_id": "m", "dimensions": {"channel": "paid"}},
    )
    path = tmp_path / "golden.json"
    recorder.save(path)

    replay = ReplayMcpClient(Cassette.load(path))
    # Argument order differs: the canonical hash is order-independent.
    out = await replay.call(
        agent_id="v3_agent",
        capability="seleric.metrics_query",
        arguments={"dimensions": {"channel": "paid"}, "metric_id": "m"},
    )
    assert out["rows"] == [{"value": 5.0}]
    assert "seleric.metrics_query" in replay.capabilities


async def test_replay_miss_raises() -> None:
    replay = ReplayMcpClient(Cassette())
    with pytest.raises(CassetteMiss):
        await replay.call(agent_id="v3_agent", capability="seleric.metrics_query", arguments={})


async def test_replay_response_is_isolated(tmp_path: Path) -> None:
    recorder = RecordingMcpClient(_Inner())
    await recorder.call(agent_id="a", capability="seleric.metrics_query", arguments={"x": 1})
    path = tmp_path / "c.json"
    recorder.save(path)
    replay = ReplayMcpClient(Cassette.load(path))

    first = await replay.call(agent_id="a", capability="seleric.metrics_query", arguments={"x": 1})
    first["rows"][0]["value"] = 999.0
    second = await replay.call(agent_id="a", capability="seleric.metrics_query", arguments={"x": 1})
    assert second["rows"][0]["value"] == 5.0


def test_capabilities_from_cassette(tmp_path: Path) -> None:
    cassette = Cassette()
    cassette.record("seleric.metrics_query", {"a": 1}, {"ok": True})
    cassette.record("seleric.catalogue_list_metrics", {}, {"ok": True})
    assert cassette.capabilities() == {
        "seleric.metrics_query",
        "seleric.catalogue_list_metrics",
    }

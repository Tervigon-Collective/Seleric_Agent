"""Contract tests for McpSemanticPort against a scripted MCP client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from seleric_swarm.semantic import FakeSemanticPort, McpSemanticPort, MetricsQueryArgs, SemanticPort

FIXTURE = json.loads(Path("tests/fixtures/catalogue/snapshot.json").read_text(encoding="utf-8"))


class ScriptedMcp:
    def __init__(self, responses: list[tuple[str, dict]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        del agent_id
        self.calls.append((capability, arguments))
        for index, (name, payload) in enumerate(self._responses):
            if capability == name:
                self._responses.pop(index)
                return payload
        return {}


def test_fake_port_matches_the_protocol() -> None:
    port = FakeSemanticPort(FIXTURE)
    assert isinstance(port, SemanticPort)
    assert port.version() == "zz-snap-1"
    assert port.read_model().has_metric("zz_sales")


@pytest.mark.asyncio
async def test_warm_parses_fixture_and_merges_brands() -> None:
    brands = {
        "brands": [{"id": "zz-brand", "name": "ZZ Brand"}],
        "default_brand_id": "zz-brand",
        "catalogue_version": "zz-snap-1",
    }
    mcp = ScriptedMcp([
        ("seleric.catalogue_bootstrap", FIXTURE),
        ("seleric.catalogue_list_brands", brands),
    ])
    port = McpSemanticPort(mcp)
    count = await port.warm()
    assert count == 3
    assert port.read_model().brands[0].name == "ZZ Brand"
    assert [name for name, _ in mcp.calls] == [
        "seleric.catalogue_bootstrap",
        "seleric.catalogue_list_brands",
    ]


@pytest.mark.asyncio
async def test_failed_warm_keeps_the_last_good_snapshot() -> None:
    mcp = ScriptedMcp([
        ("seleric.catalogue_bootstrap", FIXTURE),
        ("seleric.catalogue_list_brands", {}),
    ])
    port = McpSemanticPort(mcp)
    await port.warm()

    async def boom(*, agent_id: str, capability: str, arguments: dict) -> dict:
        del agent_id, arguments
        if capability == "seleric.catalogue_bootstrap":
            raise RuntimeError("down")
        return {}

    mcp.call = boom  # type: ignore[method-assign]
    assert await port.warm() == 3
    assert port.read_model().has_metric("zz_sales")


@pytest.mark.asyncio
async def test_empty_warm_refuses_queries() -> None:
    mcp = ScriptedMcp([])
    port = McpSemanticPort(mcp)
    await port.warm()
    response = await port.query(MetricsQueryArgs(measures=("zz_sales",)))
    assert response.error is not None
    assert response.error.code == "CATALOGUE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_removed_metric_is_a_typed_refusal() -> None:
    port = FakeSemanticPort(FIXTURE)
    response = await port.query(MetricsQueryArgs(measures=("zz_gone",)))
    assert response.error is not None
    assert response.error.code == "METRIC_NOT_FOUND"


@pytest.mark.asyncio
async def test_version_change_rewarms() -> None:
    versions = {"brands": "zz-snap-1"}

    class Flip(ScriptedMcp):
        async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
            del agent_id, arguments
            self.calls.append((capability, {}))
            if capability == "seleric.catalogue_list_brands":
                return {"catalogue_version": versions["brands"], "brands": []}
            return {"catalogue_version": "zz-snap-1", "metrics": [{"id": "zz_sales", "label": "S"}]}

    mcp = Flip([])
    port = McpSemanticPort(mcp, ttl_seconds=10_000)
    await port.warm()
    versions["brands"] = "zz-snap-2"
    await port.ensure_fresh()
    bootstrap_calls = [name for name, _ in mcp.calls if name == "seleric.catalogue_bootstrap"]
    assert len(bootstrap_calls) == 2


@pytest.mark.asyncio
async def test_metric_definitions_batch_at_ten() -> None:
    seen: list[list[str]] = []

    class Batch(ScriptedMcp):
        async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
            del agent_id
            self.calls.append((capability, arguments))
            ids = list(arguments.get("metric_ids") or [])
            seen.append(ids)
            return {"metrics": {metric_id: {"id": metric_id, "unit": "ZZU"} for metric_id in ids}}

    port = McpSemanticPort(Batch([]))
    port._model = FakeSemanticPort(FIXTURE).read_model()
    port._warmed_at = 1.0
    ids = [f"zz_{index}" for index in range(11)]
    found = await port.metric_definitions(ids)
    assert [len(batch) for batch in seen] == [10, 1]
    assert set(found) == set(ids)


def test_query_args_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MetricsQueryArgs(measures=("zz_sales",), not_a_field=1)  # type: ignore[call-arg]

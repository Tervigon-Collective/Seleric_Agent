"""Correlation id (plan P1.6): the investigation id is on log lines, span metadata, events and the stored record."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

import structlog

from seleric_swarm.diagnosis.adapters.runstore_artifacts import ArtifactRunStore
from seleric_swarm.diagnosis.adapters.runtime import SeededRuntime
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.trace import events as kinds
from seleric_swarm.diagnosis.trace.correlation import (
    current_investigation_id,
    install_log_record_attribute,
    investigation_scope,
    with_investigation,
)
from seleric_swarm.diagnosis.trace.run import RunInputs, record_run
from seleric_swarm.state.artifacts import InMemoryArtifactStore


def test_the_scope_binds_and_unbinds_the_id_in_both_logging_paths():
    assert current_investigation_id() == "" and "investigation_id" not in structlog.contextvars.get_contextvars()
    with investigation_scope("inv-a"):
        assert current_investigation_id() == "inv-a"
        assert structlog.contextvars.get_contextvars()["investigation_id"] == "inv-a"
        with investigation_scope("inv-b"):
            assert current_investigation_id() == "inv-b"
        assert current_investigation_id() == "inv-a"
    assert current_investigation_id() == "" and "investigation_id" not in structlog.contextvars.get_contextvars()


def test_every_stdlib_log_record_carries_the_id_while_inside_a_scope(caplog):
    install_log_record_attribute()
    install_log_record_attribute()  # idempotent
    log = logging.getLogger("seleric.test.correlation")
    with caplog.at_level(logging.INFO, logger="seleric.test.correlation"):
        log.info("outside")
        with investigation_scope("inv-log"):
            log.info("inside")
    outside, inside = caplog.records
    assert outside.investigation_id == "" and inside.investigation_id == "inv-log"


def test_span_metadata_gets_the_id_only_inside_a_scope():
    base = {"mission_id": "m1"}
    assert with_investigation(base) == base
    with investigation_scope("inv-span"):
        assert with_investigation(base) == {"mission_id": "m1", "investigation_id": "inv-span"}
    assert base == {"mission_id": "m1"}  # the caller's dict is not modified


def test_two_concurrent_investigations_do_not_see_each_others_ids():
    async def work(name: str) -> str:
        with investigation_scope(name):
            await asyncio.sleep(0.01)
            return current_investigation_id()

    async def both() -> list[str]:
        return list(await asyncio.gather(work("one"), work("two")))

    assert asyncio.run(both()) == ["one", "two"]


class Logs:
    kind: ClassVar[str] = "logs"

    async def run(self, inputs: RunInputs) -> dict[str, Any]:
        logging.getLogger("seleric.test.runner").warning("working")
        inputs.emitter.emit(kinds.NODE_VISITED, {"node": "n"})
        return {"seen": current_investigation_id()}


async def test_a_run_binds_its_id_for_the_runner_and_the_stored_record_carries_it(caplog):
    class Series:
        async def fetch(self, requests):
            return []

    with caplog.at_level(logging.WARNING, logger="seleric.test.runner"):
        record = await record_run(
            Logs(), investigation_id="inv-run", request={}, config=DiagnosisConfig(), seed=1, series=Series(),
            runtime=SeededRuntime(1),
        )
    assert record.report == {"seen": "inv-run"}
    assert [r.investigation_id for r in caplog.records if r.name == "seleric.test.runner"] == ["inv-run"]
    assert {e["investigation_id"] for e in record.events} == {"inv-run"}
    store = InMemoryArtifactStore()
    ArtifactRunStore(store, workspace_id="w", mission_id="m").save(record)
    [artifact] = store.list_for_mission("m")
    assert artifact.provenance.source_metadata == {"investigation_id": "inv-run"}

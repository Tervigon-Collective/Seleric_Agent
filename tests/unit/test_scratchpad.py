"""Scratchpad working-memory: dedupe, bounded cap, render, and the zero-latency
dynamic-instructions read path (null-safe for the deps=None stub run)."""

from __future__ import annotations

import pytest

from seleric_swarm.agent.agent import build_seleric_agent
from seleric_swarm.state.scratchpad import Scratchpad


def test_dedupe_and_render() -> None:
    pad = Scratchpad()
    pad.note("gross_sales=5039186 over 2026-07-01..2026-07-31")
    pad.note("  gross_sales=5039186 over 2026-07-01..2026-07-31  ")  # same, whitespace
    pad.note("")  # ignored
    assert len(pad) == 1
    rendered = pad.render()
    assert "ALREADY established" in rendered
    assert "gross_sales=5039186" in rendered


def test_empty_renders_nothing() -> None:
    assert Scratchpad().render() == ""


def test_bounded_cap_drops_oldest() -> None:
    pad = Scratchpad(max_notes=3)
    for i in range(5):
        pad.note(f"fact {i}")
    assert len(pad) == 3
    body = pad.render()
    assert "fact 0" not in body and "fact 1" not in body
    assert "fact 4" in body


async def test_stub_run_survives_null_deps() -> None:
    # agent.run with no deps must not raise from the working-memory instruction.
    agent = build_seleric_agent()
    result = await agent.run("hello")
    assert result.output.mission_id == "stub"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

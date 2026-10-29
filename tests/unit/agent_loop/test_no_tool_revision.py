"""A question about the brand's own data answered with no tool call is sent back once to use the tools (plan Phase 6)."""
from __future__ import annotations

import dataclasses

import pytest
from pydantic_ai.messages import TextPart, ToolCallPart

from seleric_swarm.agent.data_question import names_a_metric
from seleric_swarm.agent.loop import run_turn
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from tests.unit.agent_loop.test_loop import DEFS, FakeGateway, _deps, _scripted
from seleric_swarm.state.artifacts import InMemoryArtifactStore

CATALOGUE = CatalogueSnapshot(metrics=(CatalogueMetricMeta(id="net_roas", label="Net ROAS"),
                                       CatalogueMetricMeta(id="net_sales", label="Net sales")))
CALL = [ToolCallPart("metrics_query", {"measures": ["net_sales"]}, tool_call_id="c1")]


async def _turn(model, question, history_payloads=None):
    deps = dataclasses.replace(_deps(InMemoryArtifactStore()), catalogue=CATALOGUE)
    return await run_turn(deps=deps, model=model, gateway=FakeGateway(), tool_defs=DEFS, system_prompt="sys",
                          question=question, history=[], prior_payloads=history_payloads)


def test_a_metric_is_named_by_its_label_or_id_as_whole_words():
    assert names_a_metric("What is net ROAS?", CATALOGUE) and names_a_metric("show net_sales please", CATALOGUE)
    assert not names_a_metric("thanks, that is great", CATALOGUE) and not names_a_metric("What is the netsales?", CATALOGUE)


@pytest.mark.asyncio
async def test_a_data_question_answered_without_a_tool_is_sent_back_once_and_then_uses_it():
    model = _scripted([TextPart("Net ROAS is revenue over spend.")], CALL,
                      [TextPart("Net sales were ₹42,177 yesterday, up from ₹34,377.")])
    result = await _turn(model, "How is net sales doing?")
    assert result.stats["no_tool_revisions"] == 1 and result.stats["tool_calls"] == 1
    assert "42,177" in result.answer


@pytest.mark.asyncio
async def test_a_question_that_names_no_metric_is_not_sent_back():
    result = await _turn(_scripted([TextPart("You're welcome!")]), "thanks, that is great")
    assert "no_tool_revisions" not in result.stats and result.answer == "You're welcome!"

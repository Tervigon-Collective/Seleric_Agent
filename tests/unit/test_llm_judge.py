from __future__ import annotations

import pytest

from seleric_swarm.eval.llm_judge import _parse, judge_synthesis
from seleric_swarm.llm.port import LLMResponse


def test_parse_accepts_clean_json():
    v = _parse('{"faithful": 1, "extra_numbers": 0, "rationale": "ok"}')
    assert v.passed and v.parsed


def test_parse_fails_closed_on_garbage():
    v = _parse("the answer looks fine to me")
    assert not v.passed
    assert not v.parsed


class _StubLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, request):
        return LLMResponse(text=self._text, model=request.model)

    async def complete_structured(self, request, schema):  # pragma: no cover - unused
        raise NotImplementedError


@pytest.mark.asyncio
async def test_judge_synthesis_pass_and_fail():
    good = await judge_synthesis(
        _StubLLM('{"faithful": 1, "extra_numbers": 0, "rationale": "matches"}'),
        query="q",
        answer="net sales were 98000",
        claims=[{"text": "metric.net_sales was 98000"}],
        judge_model="judge",
    )
    assert good.passed

    bad = await judge_synthesis(
        _StubLLM('{"faithful": 0, "extra_numbers": 1, "rationale": "invented 42"}'),
        query="q",
        answer="net sales were 98000 and margin was 42%",
        claims=[{"text": "metric.net_sales was 98000"}],
        judge_model="judge",
    )
    assert not bad.passed


@pytest.mark.asyncio
async def test_judge_synthesis_survives_llm_error():
    class _Boom:
        async def complete(self, request):
            raise RuntimeError("judge model down")

    v = await judge_synthesis(
        _Boom(), query="q", answer="a", claims=[], judge_model="judge"
    )
    assert not v.passed and not v.parsed

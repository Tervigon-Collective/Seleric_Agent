"""Unit tests for M1.1 Understand v2."""

from __future__ import annotations

import pytest

from seleric_swarm.contracts.pipeline import Clarification, MeasurePhrase, QuestionDraft
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.understand import understand
from seleric_swarm.understand._prompt import build_prompt


def _read_model() -> SemanticReadModel:
    return SemanticReadModel.from_payload({
        "metrics": [
            {"id": "net_sales", "label": "Net Sales", "unit": "INR", "supported_dimensions": ["channel", "product_title"]},
            {"id": "gross_sales", "label": "Gross Sales", "unit": "INR", "supported_dimensions": ["channel"]},
            {"id": "ad_spend", "label": "Ad Spend", "unit": "INR", "supported_dimensions": ["channel", "sub_channel"]},
        ],
        "dimensions": [
            {"id": "channel", "allowed_values": ["meta", "google", "amazon"]},
            {"id": "product_title", "allowed_values": ["Widget A", "Widget B"]},
            {"id": "sub_channel", "allowed_values": ["facebook", "instagram"]},
        ],
        "hierarchies": [
            {"id": "traffic", "levels": ["channel", "sub_channel", "campaign"]}
        ],
        "views": [],
        "brands": [],
        "scope_dimensions": ["brand_id"],
    })


def test_build_prompt_contains_catalogue_labels():
    """Prompt should contain catalogue labels (human-readable), not metric ids."""
    rm = _read_model()
    prompt = build_prompt(
        "What were net sales yesterday?",
        read_model=rm,
        value_words=["yesterday"],
    )
    # Should contain labels
    assert "Net Sales" in prompt
    assert "Gross Sales" in prompt
    assert "Ad Spend" in prompt
    # Should contain dimension names
    assert "channel" in prompt
    assert "product_title" in prompt
    assert "sub_channel" in prompt
    # Should contain hierarchy
    assert "traffic" in prompt


def test_build_prompt_no_metric_ids():
    """Prompt must NOT contain metric ids (A2 rule)."""
    rm = _read_model()
    prompt = build_prompt(
        "Show me net sales by channel",
        read_model=rm,
        value_words=[],
    )
    # Must not contain metric ids
    assert "net_sales" not in prompt
    assert "gross_sales" not in prompt
    assert "ad_spend" not in prompt


def test_build_prompt_no_channel_words():
    """Prompt must NOT contain channel words as concepts (A2 rule)."""
    rm = _read_model()
    prompt = build_prompt(
        "Show me meta spend",
        read_model=rm,
        value_words=["meta"],
        value_meanings={"meta": "channel"},
    )
    # The word "meta" appears in value_words section, but not as a concept word
    # in the catalogue vocabulary section
    vocab_section = prompt.split("Catalogue vocabulary")[1] if "Catalogue vocabulary" in prompt else ""
    assert "meta" not in vocab_section.lower()


def test_build_prompt_includes_value_words():
    """Value words and their meanings should appear in prompt."""
    rm = _read_model()
    prompt = build_prompt(
        "Show me meta and google spend",
        read_model=rm,
        value_words=["meta", "google"],
        value_meanings={"meta": "channel", "google": "channel"},
    )
    assert "meta" in prompt
    assert "google" in prompt
    assert "recorded as channel" in prompt


def test_build_prompt_with_prior_turn():
    """Prior question and offer should appear in prompt."""
    rm = _read_model()
    prompt = build_prompt(
        "What about google?",
        read_model=rm,
        value_words=["google"],
        prior_question="What were net sales yesterday?",
        prior_offer="Would you like me to break it down by channel?",
    )
    assert "What were net sales yesterday" in prompt
    # The offer gets truncated at 300 chars, so check for a substring
    assert "break it down by channel" in prompt


def test_build_prompt_with_exemplars():
    """Verified examples should appear in prompt."""
    rm = _read_model()
    prompt = build_prompt(
        "Show me sales",
        read_model=rm,
        value_words=[],
        exemplars=[{
            "question": "What were net sales last week?",
            "kind": "analysis",
            "shape": "lookup",
            "measures": [{"phrase": "net sales", "metric_id": "net_sales"}],
        }]
    )
    assert "Verified examples" in prompt
    assert "What were net sales last week" in prompt
    assert "lookup" in prompt


def test_question_draft_model_validates():
    draft = QuestionDraft(kind="analysis", shape="lookup", measures=(MeasurePhrase(words="net sales"),))
    assert draft.kind == "analysis" and draft.shape == "lookup" and len(draft.measures) == 1


def test_question_draft_rejects_extra_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        QuestionDraft(kind="analysis", shape="lookup", extra_field="not allowed")  # type: ignore[call-arg]


def test_clarification_model():
    clar = Clarification(reason="ambiguous_dimension", question_to_user="Which one?", options=("a", "b"))
    assert clar.options == ("a", "b")


# --- the call and its failure policy (A4) -------------------------------------------------------

class _ScriptedLLM:
    """A DraftLLM that replays a script: an exception or an output per attempt."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    async def __call__(self, *, instructions, prompt):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, {"input_tokens": 1, "output_tokens": 1}


async def test_understand_returns_the_draft():
    draft = QuestionDraft(kind="analysis", shape="lookup", measures=(MeasurePhrase(words="sales"),))
    llm = _ScriptedLLM(draft)
    out, stats = await understand(llm, "sales yesterday", read_model=_read_model())
    assert out == draft and stats["status"] == "ok" and llm.calls == 1


async def test_understand_retries_once_then_succeeds():
    draft = QuestionDraft(kind="analysis", shape="lookup")
    llm = _ScriptedLLM(RuntimeError("429"), draft)
    out, stats = await understand(llm, "q", read_model=_read_model())
    assert out == draft and stats["attempt"] == 2


async def test_understand_asks_after_two_failures_and_never_raises():
    llm = _ScriptedLLM(RuntimeError("429"), TimeoutError())
    out, stats = await understand(llm, "q", read_model=_read_model())
    assert isinstance(out, Clarification) and out.reason == "understanding_unavailable"
    assert llm.calls == 2 and stats["status"] == "failed"


async def test_understand_without_a_model_asks():
    out, stats = await understand(None, "q", read_model=_read_model())
    assert isinstance(out, Clarification) and stats["status"] == "skipped"


async def test_understand_passes_a_model_clarification_through():
    clar = Clarification(reason="ambiguous_measure", question_to_user="Gross or net?")
    out, stats = await understand(_ScriptedLLM(clar), "q", read_model=_read_model())
    assert out == clar and stats["status"] == "clarification"


async def test_pydantic_ai_adapter_returns_structured_draft():
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from seleric_swarm.adapters.understand_llm import PydanticAiDraftLLM

    def reply(messages, info: AgentInfo) -> ModelResponse:
        tool = next(t for t in info.output_tools if "QuestionDraft" in t.name or "question" in t.name.lower())
        return ModelResponse(parts=[ToolCallPart(tool.name, {"kind": "analysis", "shape": "trend"})])

    out, usage = await PydanticAiDraftLLM(FunctionModel(reply))(instructions="x", prompt="y")
    assert isinstance(out, QuestionDraft) and out.shape == "trend" and "input_tokens" in usage

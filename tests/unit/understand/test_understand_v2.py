"""Unit tests for M1.1 Understand v2."""

from __future__ import annotations

import pytest

from seleric_swarm.understand._prompt import build_prompt
from seleric_swarm.understand.draft import (
    Clarification,
    MeasureExpr,
    QuestionDraft,
    WindowExpr,
)
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta


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
    """QuestionDraft should validate with required fields."""
    draft = QuestionDraft(
        kind="analysis",
        shape="lookup",
        measures=[{"phrase": "net sales"}],
    )
    assert draft.kind == "analysis"
    assert draft.shape == "lookup"
    assert len(draft.measures) == 1


def test_question_draft_rejects_extra_fields():
    """QuestionDraft should reject extra fields (frozen, extra=forbid)."""
    with pytest.raises(Exception):
        QuestionDraft(
            kind="analysis",
            shape="lookup",
            measures=[{"phrase": "net sales"}],
            extra_field="not allowed",
        )


def test_clarification_model():
    """Clarification model should work."""
    clar = Clarification(
        reason="ambiguous_dimension",
        question_to_user="Did you mean channel or sub_channel?",
        options=("channel", "sub_channel"),
    )
    assert clar.reason == "ambiguous_dimension"
    assert clar.options == ("channel", "sub_channel")
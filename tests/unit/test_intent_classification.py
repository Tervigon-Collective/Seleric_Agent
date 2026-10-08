"""Jev typed-decision helpers still used by tools, and the routing-hint rendering."""

from __future__ import annotations

import pytest

from seleric_swarm.agent.intent import (
    _RISK_LEVELS,
    QueryClassification,
    _normalize_bool,
    _normalize_ordinal,
    select_chart_type,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True), ("yes", True), ("1", True), (0.83, True), (0.5, True),
        (False, False), ("no", False), (0.02, False), ("maybe", None),
    ],
)
def test_normalize_bool(value, expected) -> None:
    assert _normalize_bool(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(0, "low"), (0.4, "low"), (1.0, "medium"), (2, "high"), (9, "high"), ("x", None), (True, None)],
)
def test_normalize_risk_ordinal(value, expected) -> None:
    assert _normalize_ordinal(value, _RISK_LEVELS) == expected


def test_routing_hint_only_shows_signal() -> None:
    from seleric_swarm.agent.runner import _routing_hint

    # none/either/False carry no signal → empty hint.
    assert _routing_hint(QueryClassification()) == ""
    assert _routing_hint(
        QueryClassification(grain="none", period="none", direction="either", depends_on_prior=False)
    ) == ""
    hint = _routing_hint(
        QueryClassification(grain="day", period="custom_date_range", direction="decrease",
                            depends_on_prior=True)
    )
    assert "grain=day" in hint and "period=custom_date_range" in hint
    assert "direction=decrease" in hint and "follow_up=true" in hint
    assert "never invent dates" in hint


def test_routing_hint_sends_why_questions_to_the_diagnosis_tool() -> None:
    from seleric_swarm.agent.runner import _routing_hint

    hint = _routing_hint(QueryClassification(intent="diagnostic"))
    assert "diagnose_metric_change" in hint
    assert "diagnose_metric_change" not in _routing_hint(QueryClassification(intent="lookup"))


@pytest.mark.parametrize(
    ("text", "grain"),
    [
        ("gross revenue last week all days", "day"),
        ("give me daily chart of ad spend for the last 30 days", "day"),
        ("net sales day-wise for September", "day"),
        ("orders by week this quarter", "week"),
        ("monthly revenue this year", "month"),
        ("net profit last 30 days by order date", None),  # a date basis, not a bucket
        ("what were net sales last month", None),
        ("daily and monthly sales", None),  # two buckets named: the agent decides
    ],
)
def test_stated_grain_is_read_from_the_words(text, grain):
    from seleric_swarm.agent.intent import stated_grain

    assert stated_grain(text) == grain


@pytest.mark.asyncio
async def test_select_chart_type_uses_jev_choice(monkeypatch):
    async def _fake_post(state, questions, **kwargs):
        assert "stacked bar" in state
        assert "time_periods=7" in state
        assert questions["chart_type"]["type"] == "choice"
        return {"chart_type": {"type": "choice", "choice": "bar"}}

    monkeypatch.setattr("seleric_swarm.agent.intent._post_jev", _fake_post)
    assert (
        await select_chart_type(
            "stacked bar graph for the net profit waterfall",
            data_shape="time_periods=7; metrics=1; category_values=0; values_may_be_negative=true",
            title="Net profit waterfall",
            base_url="http://jev.test",
            api_key="k",
        )
        == "bar"
    )


@pytest.mark.asyncio
async def test_select_chart_type_fails_open_without_config():
    assert (
        await select_chart_type(
            "trend",
            data_shape="time_periods=7; metrics=1; category_values=0",
            base_url="",
            api_key="",
        )
        is None
    )

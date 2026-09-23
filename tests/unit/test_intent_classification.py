"""Unit tests for the enriched Jev multi-question classification.

One Jev call now returns intent + complexity + needs_write in parallel. The
response parsing is tolerant (undocumented answer shape) and per-field
fail-open, and the whole thing fails open to an empty classification on any
error or missing credentials.
"""

from __future__ import annotations

import pytest

from seleric_swarm.agent import intent as intent_mod
from seleric_swarm.agent.intent import (
    _COMPLEXITY_LEVELS,
    _RISK_LEVELS,
    QueryClassification,
    _normalize_bool,
    _normalize_ordinal,
    _parse_answers,
    classify_query,
)


def _normalize_complexity(value):
    return _normalize_ordinal(value, _COMPLEXITY_LEVELS)


def test_parse_full_answers() -> None:
    # Real Jev shape: score is a float, noul is P(true).
    answers = {
        "intent": {"type": "choice", "choice": "diagnostic"},
        "complexity": {"type": "score", "score": 1.8, "confidence": 0.7},
        "needs_write": {"type": "noul", "noul": 0.02},
    }
    result = _parse_answers(answers)
    assert result.intent == "diagnostic"
    assert result.complexity == "complex"
    assert result.needs_write is False


def test_parse_is_per_field_fail_open() -> None:
    # Unknown intent + garbled complexity → those fields None, others survive.
    answers = {
        "intent": {"choice": "not_a_real_label"},
        "complexity": {"score": "banana"},
        "needs_write": {"choice": "yes"},
    }
    result = _parse_answers(answers)
    assert result.intent is None
    assert result.complexity is None
    assert result.needs_write is True


def test_parse_non_dict_is_empty() -> None:
    assert _parse_answers(None) == QueryClassification()
    assert _parse_answers("nope") == QueryClassification()


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "simple"), (0.4, "simple"), (1.8, "complex"), (1.4, "moderate"),
        ("1", "moderate"), ("complex", "complex"), (2, "complex"), (9, "complex"),
        ("x", None), (True, None),
    ],
)
def test_normalize_complexity(value, expected) -> None:
    assert _normalize_complexity(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True), ("yes", True), ("1", True), (0.83, True), (0.5, True),
        (False, False), ("no", False), (0.02, False), ("maybe", None),
    ],
)
def test_normalize_bool(value, expected) -> None:
    assert _normalize_bool(value) == expected


def test_parse_new_signals() -> None:
    # grain / period / direction (choice) + depends_on_prior (noul).
    answers = {
        "grain": {"choice": "day"},
        "period": {"choice": "custom_date_range"},
        "direction": {"choice": "decrease"},
        "depends_on_prior": {"noul": 0.8},
    }
    result = _parse_answers(answers)
    assert result.grain == "day"
    assert result.period == "custom_date_range"
    assert result.direction == "decrease"
    assert result.depends_on_prior is True


def test_parse_new_signals_fail_open() -> None:
    # Labels we did not offer are dropped to None (never trusted blindly).
    result = _parse_answers(
        {"grain": {"choice": "fortnight"}, "period": {"choice": "someday"},
         "direction": {"choice": "sideways"}}
    )
    assert result.grain is None
    assert result.period is None
    assert result.direction is None


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


@pytest.mark.asyncio
async def test_classify_query_no_credentials_is_empty() -> None:
    result = await classify_query("net sales", base_url="", api_key="")
    assert result == QueryClassification()


@pytest.mark.asyncio
async def test_classify_query_http_error_fails_open(monkeypatch) -> None:
    class _BoomClient:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a) -> None: ...
        async def post(self, *a, **k):
            raise RuntimeError("jev down")

    monkeypatch.setattr(intent_mod.httpx, "AsyncClient", _BoomClient)
    result = await classify_query("q", base_url="http://jev", api_key="k")
    assert result == QueryClassification()


@pytest.mark.asyncio
async def test_classify_query_parses_live_shape(monkeypatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None: ...
        def json(self):
            return {
                "answers": {
                    "intent": {"type": "choice", "choice": "lookup"},
                    "complexity": {"type": "score", "score": 0.1, "confidence": 0.9},
                    "needs_write": {"type": "noul", "noul": 0.03},
                }
            }

    class _Client:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a) -> None: ...
        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(intent_mod.httpx, "AsyncClient", _Client)
    result = await classify_query("net sales", base_url="http://jev", api_key="k")
    assert result.intent == "lookup"
    assert result.complexity == "simple"
    assert result.needs_write is False

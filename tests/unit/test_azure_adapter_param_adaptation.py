"""The LLMPort azure adapter learns per-model param quirks from the API's own
400s — reasoning models (gpt-5*/o-series) want max_completion_tokens, not
max_tokens, and reject a custom temperature."""

from __future__ import annotations

from types import SimpleNamespace

from seleric_swarm.llm.adapters.azure_openai_compatible import (
    AzureOpenAICompatibleAdapter,
    _unsupported_param_fix,
)


def _err(status, body):
    return SimpleNamespace(status_code=status, body=body)


def test_detects_max_tokens_unsupported_by_param():
    exc = _err(400, {"error": {"code": "unsupported_parameter", "param": "max_tokens",
                               "message": "Use 'max_completion_tokens' instead."}})
    assert _unsupported_param_fix(exc) == "max_completion_tokens"


def test_detects_max_tokens_unsupported_by_message_only():
    exc = _err(400, {"error": {"message": "please use max_completion_tokens"}})
    assert _unsupported_param_fix(exc) == "max_completion_tokens"


def test_detects_temperature_unsupported():
    exc = _err(400, {"error": {"code": "unsupported_value", "param": "temperature",
                               "message": "temperature does not support 0"}})
    assert _unsupported_param_fix(exc) == "drop_temperature"


def test_non_400_and_unrelated_400_return_none():
    assert _unsupported_param_fix(_err(500, {})) is None
    assert _unsupported_param_fix(_err(400, {"error": {"param": "messages"}})) is None


def test_apply_param_fixes_renames_tokens_and_drops_temperature():
    adapter = AzureOpenAICompatibleAdapter.__new__(AzureOpenAICompatibleAdapter)
    adapter._param_fixes = {"gpt-5-mini": {"max_completion_tokens", "drop_temperature"}}
    base = {"model": "gpt-5-mini", "max_tokens": 2000, "temperature": 0, "messages": []}

    out = adapter._apply_param_fixes(base, "gpt-5-mini")

    assert "max_tokens" not in out
    assert out["max_completion_tokens"] == 2000
    assert "temperature" not in out
    # A model with no learned fixes is passed through untouched.
    assert adapter._apply_param_fixes(base, "gpt-4o") == base

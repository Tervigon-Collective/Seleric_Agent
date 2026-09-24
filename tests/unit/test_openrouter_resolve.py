"""OpenRouter appends to the V3 fallback chain — Azure first, OpenRouter tail.

Pure composition check: no network. Confirms resolve_v3_model appends the
OPENROUTER_MODELS after every Azure model, in order, and only when both a key
and models are configured. Mirrors tests/unit/test_runner_jev_routing.py.
"""

from __future__ import annotations

from pydantic_ai.models.fallback import FallbackModel

from seleric_swarm.agent.model import resolve_v3_model
from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.openrouter import resolved_openrouter_models


def _names(model) -> list[str]:
    models = model.models if isinstance(model, FallbackModel) else [model]
    return [getattr(m, "model_name", str(m)) for m in models]


def _settings(**kw) -> Settings:
    base = {
        "llm_provider": "azure_openai_compatible",
        "azure_openai_endpoint": "https://x",
        "azure_openai_api_key": "k",
        "azure_openai_models": "strong-1,strong-2",
        # Hermetic: ignore the developer's .env (a real ENDPOINT_2 would append
        # extra Azure models and break the exact-chain assertions below).
        "azure_openai_endpoint_2": "",
        "azure_openai_api_key_2": "",
        "azure_openai_models_2": "",
        "azure_openai_fast_model": "",
        # Forced empty so a developer's real OPENROUTER_* in .env (loaded into
        # os.environ by tests/conftest.py) can't leak in — init kwargs outrank
        # env in pydantic-settings. Individual tests override via kw.
        "openrouter_api_key": "",
        "openrouter_models": "",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_openrouter_appended_after_azure() -> None:
    names = _names(
        resolve_v3_model(
            _settings(
                openrouter_api_key="or-key",
                openrouter_models="openai/gpt-4o-mini,deepseek/deepseek-chat",
            )
        )
    )
    # Azure models keep priority; OpenRouter is strictly the tail, in order.
    assert names == ["strong-1", "strong-2", "openai/gpt-4o-mini", "deepseek/deepseek-chat"]


def test_no_openrouter_when_key_missing() -> None:
    names = _names(_resolve_or(_settings(openrouter_models="openai/gpt-4o-mini")))
    assert names == ["strong-1", "strong-2"]


def test_no_openrouter_when_models_missing() -> None:
    names = _names(_resolve_or(_settings(openrouter_api_key="or-key")))
    assert names == ["strong-1", "strong-2"]


def _resolve_or(settings: Settings):
    return resolve_v3_model(settings)


def test_resolved_openrouter_models_parses_json_and_csv() -> None:
    assert resolved_openrouter_models(_settings(openrouter_models="a,b, c")) == ["a", "b", "c"]
    assert resolved_openrouter_models(
        _settings(openrouter_models='["a", "b"]')
    ) == ["a", "b"]
    assert resolved_openrouter_models(_settings()) == []

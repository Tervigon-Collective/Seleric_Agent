import pytest

from seleric_swarm.config.settings import Settings, configured_chat_model
from seleric_swarm.conversations.events import InMemoryEventNotifier, build_event_notifier
from seleric_swarm.observability.tracing import (
    REQUIRED_SPAN_METADATA,
    mission_metadata,
    redact_mapping,
)


def test_configured_chat_model_uses_models_list_when_singular_empty():
    settings = Settings(
        _env_file=None,
        azure_openai_model="",
        azure_openai_models='["DeepSeek-V4-Flash","gpt-5-mini"]',
    )
    assert settings.primary_model() == "DeepSeek-V4-Flash"
    assert configured_chat_model(settings) == "DeepSeek-V4-Flash"
    settings = Settings(azure_openai_api_key="", langsmith_api_key="")
    assert settings.azure_openai_api_key == ""
    assert settings.langsmith_api_key == ""


def test_settings_do_not_hardcode_endpoints():
    """URLs and credentials must come from env — no production defaults in code."""
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint="",
        azure_openai_api_key="",
        azure_openai_model="",
        database_url="",
        seleric_mcp_url="",
        seleric_mcp_token="",
        langsmith_endpoint="",
        a2a_public_base_url="",
        api_host="",
        api_port=0,
    )
    assert settings.azure_openai_endpoint == ""
    assert settings.database_url == ""
    assert settings.seleric_mcp_url == ""
    assert settings.langsmith_endpoint == ""
    assert settings.a2a_public_base_url == ""
    assert settings.api_host == ""
    assert settings.api_port == 0


def test_event_notifier_selection_defaults_safely_and_selects_durable_redis():
    local = Settings(_env_file=None)
    assert local.resolved_event_notifier_backend() == "memory"
    assert isinstance(build_event_notifier("memory"), InMemoryEventNotifier)

    durable = Settings(
        _env_file=None,
        app_env="production",
        persistence_backend="postgres",
        database_url="postgresql://database",
        redis_url="redis://redis:6379/0",
    )
    assert durable.resolved_event_notifier_backend() == "redis"
    assert (
        durable.model_copy(update={"event_notifier_backend": "memory"})
        .resolved_event_notifier_backend()
        == "memory"
    )


def test_explicit_redis_notifier_requires_url():
    with pytest.raises(ValueError, match="requires redis_url"):
        build_event_notifier("redis")


def test_bootstrap_wires_resolved_event_notifier(settings, monkeypatch):
    from seleric_swarm import bootstrap

    selected: list[tuple[str, str]] = []
    notifier = InMemoryEventNotifier()

    def build(backend: str, *, redis_url: str):
        selected.append((backend, redis_url))
        return notifier

    monkeypatch.setattr(bootstrap, "build_event_notifier", build)
    runtime = bootstrap.build_runtime(settings)
    assert selected == [("memory", settings.redis_url)]
    assert runtime.activity_events is not None
    assert runtime.activity_events.notifier is notifier

    redis_settings = settings.model_copy(
        update={
            "event_notifier_backend": "redis",
            "redis_url": "redis://redis:6379/0",
        }
    )
    redis_runtime = bootstrap.build_runtime(redis_settings)
    assert selected[-1] == ("redis", redis_settings.redis_url)
    assert redis_runtime.activity_events is not None
    assert redis_runtime.activity_events.notifier is notifier


def test_placeholder_secrets_are_stripped():
    settings = Settings(azure_openai_api_key="replace_me", langsmith_api_key="changeme")
    assert settings.azure_openai_api_key == ""
    assert settings.langsmith_api_key == ""


def test_redaction_masks_keys():
    redacted = redact_mapping(
        {
            "azure_openai_api_key": "super-secret",
            "nested": {"authorization": "Bearer abc"},
            "query": "What were net sales?",
        }
    )
    assert redacted["azure_openai_api_key"] == "[REDACTED]"
    assert redacted["nested"]["authorization"] == "[REDACTED]"
    assert redacted["query"] == "What were net sales?"


def test_mission_metadata_has_required_keys():
    meta = mission_metadata(
        request_id="r1",
        session_id="s1",
        mission_id="m1",
        workflow_name="lookup_v1",
        workflow_version="1.0.0",
        agent_name="coordinator_agent",
        agent_version="0.1.0",
    )
    for key in REQUIRED_SPAN_METADATA:
        assert meta.get(key)

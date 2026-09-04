from seleric_swarm.config.settings import Settings
from seleric_swarm.observability.tracing import (
    REQUIRED_SPAN_METADATA,
    mission_metadata,
    redact_mapping,
)


def test_settings_do_not_default_secrets():
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

"""ChatGPT plan sign-in and the Claude Code CLI connection."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from pydantic_ai.models.openai import OpenAIResponsesModel

from seleric_swarm.config.settings import Settings
from seleric_swarm.llm import models_store as store
from seleric_swarm.llm.adapters.subscription import text_from_sse
from seleric_swarm.llm.chatgpt_plan import authorize_url, credential_from_token
from seleric_swarm.llm.claude_code import _as_response, status
from seleric_swarm.llm.errors import LLMError


@pytest.fixture(autouse=True)
def _models_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SELERIC_MODELS_FILE", str(tmp_path / "models.json"))
    monkeypatch.setenv("SELERIC_MODELS_KEY", Fernet.generate_key().decode())


def test_sign_in_returns_to_the_open_site():
    from urllib.parse import parse_qs, urlsplit

    from seleric_swarm.llm.chatgpt_plan import begin_login, callback_redirect, complete_from_address, finish_login

    started = begin_login("https://agent.seleric.com/ui/")
    redirect = parse_qs(urlsplit(started["url"]).query)["redirect_uri"][0]
    assert redirect == "https://agent.seleric.com/auth/callback"
    assert callback_redirect("http://10.0.0.8:8000/ui/") == "http://10.0.0.8:8000/auth/callback"
    with pytest.raises(store.ModelsError):
        callback_redirect("javascript:alert(1)")
    with pytest.raises(store.ModelsError, match="Paste the full address"):
        complete_from_address(started["login_id"], "https://evil.example/auth/callback?code=secret-code&state=x")
    with pytest.raises(store.ModelsError, match="not for this sign-in") as caught:
        complete_from_address(started["login_id"], "https://agent.seleric.com/auth/callback?code=secret-code&state=nope")
    assert "secret-code" not in str(caught.value)
    page = finish_login({"code": "secret-code", "state": "missing"})
    assert "secret-code" not in page
    assert "no longer valid" in page


def test_chatgpt_sign_in_url_is_the_official_flow_not_the_codex_client():
    url = authorize_url(
        redirect="http://127.0.0.1:1455/auth/callback", state="s", nonce="n", challenge="c",
        host="urn:uuid:00000000-0000-4000-8000-000000000001", client_id="dynamic_agent_client",
    )
    assert url.startswith("https://auth.openai.com/api/accounts/authorize?")
    assert "client_id=dynamic_agent_client" in url
    assert "chatgpt.tokens.use.direct" in url
    assert "127.0.0.1" in url
    assert "backend-api" not in url
    assert "codex_cli" not in url


def test_plan_scope_is_required_and_the_token_is_encrypted():
    with pytest.raises(store.ModelsError, match="not granted"):
        credential_from_token({"access_token": "a", "refresh_token": "r", "scope": "openid"}, {}, client_id="oaiapp_x")
    secret = credential_from_token(
        {"access_token": "atk-secret", "refresh_token": "rtk-secret", "scope": "openid chatgpt.tokens.use.direct", "expires_in": 60},
        {"sub": "user"}, client_id="oaiapp_x",
    )
    row = store.add_oauth_connection("chatgpt", json.dumps(secret), hint="plan")
    raw = store._path().read_text()
    assert "atk-secret" not in raw and "rtk-secret" not in raw
    assert store.connections(Settings())[row["id"]].auth == "oauth"
    with pytest.raises(store.ModelsError, match="sign-in"):
        store.add_connection("chatgpt", "sk-nope")


def test_claude_sign_in_stores_the_plan_and_not_the_account(monkeypatch):
    monkeypatch.setattr(
        "seleric_swarm.llm.claude_code.subprocess.run",
        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": json.dumps({"loggedIn": True, "subscriptionType": "pro", "email": "person@example.com"}), "stderr": ""})(),
    )
    monkeypatch.setattr("seleric_swarm.llm.claude_code.binary", lambda: "claude")
    assert status() == {"plan": "pro", "method": ""}
    row = store.add_cli_connection("claude-code", hint="pro")
    saved = store._path().read_text()
    assert "person@example.com" not in saved
    assert store.connections(Settings())[row["id"]].api_key is None


def test_a_tool_reply_from_claude_becomes_a_tool_call():
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.tools import ToolDefinition

    parameters = ModelRequestParameters(function_tools=[ToolDefinition(name="today_total", description="metric")])
    response = _as_response("sonnet", '{"tool":"today_total","arguments":{"metric":"orders"}}', parameters)
    assert response.parts[0].tool_name == "today_total"


def test_chatgpt_plan_failures_do_not_return_partial_text():
    body = 'data: {"type":"response.output_text.delta","delta":"no"}\ndata: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded","message":"limit"}}}\n'
    with pytest.raises(LLMError, match="limit"):
        text_from_sse(body)


def test_loop_uses_responses_for_chatgpt_and_the_cli_for_claude():
    from pydantic_ai.models.fallback import FallbackModel

    from seleric_swarm.agent.model import resolve_loop_model
    from seleric_swarm.llm.claude_code import ClaudeCodeModel as ClaudeModel

    secret = credential_from_token(
        {"access_token": "atk", "refresh_token": "rtk", "scope": "chatgpt.tokens.use.direct", "expires_in": 999999},
        {"sub": "user"}, client_id="oaiapp_x",
    )
    chat = store.add_oauth_connection("chatgpt", json.dumps(secret))
    claude = store.add_cli_connection("claude-code", hint="pro")
    settings = Settings(llm_provider="azure_openai_compatible", azure_openai_api_key="az", azure_openai_endpoint="https://azure.example.invalid")
    store.save_roles({"main": [{"connection": chat["id"], "model": "gpt-4.1"}, {"connection": claude["id"], "model": "sonnet"}], "helper": []}, None)
    model = resolve_loop_model(settings)
    chain = list(model.wrapped.models) if isinstance(model.wrapped, FallbackModel) else [model.wrapped]
    assert isinstance(chain[0], OpenAIResponsesModel)
    assert chain[0].profile["openai_responses_requires_streaming"] is True
    assert str(chain[0].client.base_url).startswith("https://api.openai.com/v1")
    assert isinstance(chain[1], ClaudeModel)

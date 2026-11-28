"""Provider-routed model chains (2026-11-21): OpenAI platform ahead of Azure, per chain entry."""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.test import TestModel

from seleric_swarm.agent.model import resolve_loop_model
from seleric_swarm.config.settings import Settings, parse_model_list, split_provider
from seleric_swarm.llm.errors import LLMError, LLMErrorCode
from seleric_swarm.llm.factory import ProviderRouter, build_llm
from seleric_swarm.llm.gateway import LLMGateway
from seleric_swarm.llm.model_spec import ModelSpec
from seleric_swarm.llm.port import ChatMessage, LLMRequest, LLMResponse

@pytest.fixture(autouse=True)
def _models_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SELERIC_MODELS_FILE", str(tmp_path / "models.json"))
    monkeypatch.setenv("SELERIC_MODELS_KEY", Fernet.generate_key().decode())


AZURE = {"azure_openai_api_key": "az", "azure_openai_endpoint": "https://azure.example.invalid"}


def _settings(**update) -> Settings:
    return Settings(llm_provider="azure_openai_compatible", **{**AZURE, **update})


def test_model_lists_and_provider_prefixes():
    assert parse_model_list('["openai:gpt-5-mini", "gpt-4o-code"]') == ["openai:gpt-5-mini", "gpt-4o-code"]
    assert parse_model_list("a, b,,") == ["a", "b"]
    assert parse_model_list("") == []
    assert split_provider("openai:gpt-5-nano") == ("openai", "gpt-5-nano")
    assert split_provider("Azure:gpt-5-mini") == ("azure", "gpt-5-mini")
    # No known prefix is an Azure deployment name, colons and all.
    assert split_provider("Llama-4-Maverick") == ("azure", "Llama-4-Maverick")
    assert split_provider("other:x") == ("azure", "other:x")


def test_loop_chain_defaults_to_the_azure_models():
    assert _settings(azure_openai_models='["gpt-5-mini","gpt-4o-code"]').loop_chain() == [
        ("azure", "gpt-5-mini"),
        ("azure", "gpt-4o-code"),
    ]
    chain = _settings(llm_models='["openai:gpt-5-mini","gpt-4o-code"]').loop_chain()
    assert chain == [("openai", "gpt-5-mini"), ("azure", "gpt-4o-code")]


def _loop_models(model) -> list:
    chain = model.wrapped
    return list(chain.models) if isinstance(chain, FallbackModel) else [chain]


def _base(model) -> str:
    inner = getattr(model, "wrapped", model)  # EffortModel wraps reasoning models
    return str(inner.client.base_url)


def test_loop_routes_each_entry_to_its_provider():
    settings = _settings(SELERIC_OPENAI_API_KEY="sk", llm_models='["openai:gpt-5-mini","azure:gpt-5-mini"]')
    models = asyncio.run(_resolve(settings))
    assert [m.model_name for m in models] == ["gpt-5-mini", "gpt-5-mini"]
    assert _base(models[0]).startswith("https://api.openai.com/v1")
    assert _base(models[1]).startswith("https://azure.example.invalid")
    # Same model on two vendors: each has its own cooldown.
    keys = {getattr(m, "wrapped", m)._health_key for m in models}
    assert keys == {"loop:openai:gpt-5-mini", "loop:azure:gpt-5-mini"}


async def _resolve(settings: Settings) -> list:
    return _loop_models(resolve_loop_model(settings))


def test_entries_without_a_key_are_dropped_and_no_key_at_all_is_the_stub():
    models = asyncio.run(_resolve(_settings(llm_models='["openai:gpt-5-mini","azure:gpt-4o-code"]')))
    assert [m.model_name for m in models] == ["gpt-4o-code"]
    stub = resolve_loop_model(Settings(llm_provider="azure_openai_compatible", azure_openai_api_key="", llm_models="openai:x"))
    assert isinstance(stub, TestModel)


class _Fake:
    def __init__(self, error: LLMError | None = None) -> None:
        self.error = error
        self.seen: list[str] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.seen.append(request.model)
        if self.error:
            raise self.error
        return LLMResponse(text="ok", model=request.model)

    async def complete_structured(self, request, schema):  # pragma: no cover - not exercised
        raise NotImplementedError


def _request(model: str = "ignored") -> LLMRequest:
    return LLMRequest(messages=[ChatMessage(role="user", content="hi")], model=model)


def test_router_sends_the_bare_model_to_its_provider_and_labels_the_vendor():
    openai, azure = _Fake(), _Fake()
    router = ProviderRouter({"openai": openai, "azure": azure})
    response = asyncio.run(router.complete(_request("openai:gpt-5-nano")))
    assert openai.seen == ["gpt-5-nano"] and azure.seen == []
    assert response.model == "openai:gpt-5-nano"


def test_helper_chain_falls_back_across_providers_on_a_rate_limit():
    openai = _Fake(LLMError(LLMErrorCode.RATE_LIMIT, "429", retryable=True))
    azure = _Fake()
    gateway = LLMGateway(
        ProviderRouter({"openai": openai, "azure": azure}),
        [ModelSpec(id="openai:gpt-5-nano", priority=0), ModelSpec(id="azure:gpt-5-mini", priority=1)],
    )
    response = asyncio.run(gateway.complete(_request()))
    assert openai.seen == ["gpt-5-nano"] and azure.seen == ["gpt-5-mini"]
    assert response.model == "azure:gpt-5-mini"


def test_a_rejected_key_moves_to_the_next_provider():
    openai = _Fake(LLMError(LLMErrorCode.AUTH, "401", retryable=False))
    azure = _Fake()
    gateway = LLMGateway(
        ProviderRouter({"openai": openai, "azure": azure}),
        [ModelSpec(id="openai:gpt-5-nano", priority=0), ModelSpec(id="azure:gpt-5-mini", priority=1)],
    )
    assert asyncio.run(gateway.complete(_request())).model == "azure:gpt-5-mini"


def test_build_llm_uses_the_helper_chain_and_skips_keyless_providers():
    llm = build_llm(_settings(llm_helper_models='["openai:gpt-5-nano","azure:gpt-5-mini"]'))
    assert isinstance(llm, LLMGateway)
    assert [spec.id for spec in llm._models] == ["azure:gpt-5-mini"]
    # Nothing usable in the helper list: the legacy single-adapter path, which needs the Azure key as before.
    with pytest.raises(LLMError):
        build_llm(Settings(llm_provider="azure_openai_compatible", llm_helper_models="openai:gpt-5-nano"))


def test_readiness_counts_any_configured_provider():
    assert _settings(SELERIC_OPENAI_API_KEY="sk").provider_key("openai") == "sk"
    assert _settings().provider_key("openai") == ""
    assert _settings(azure_openai_endpoint="").provider_key("azure") == ""


def test_the_company_key_is_not_the_sdk_default_env_name(monkeypatch):
    # The voice worker builds an OpenAI client without a key; a shared OPENAI_API_KEY would leak into it.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-sdk")
    assert Settings(_env_file=None).openai_api_key == ""
    monkeypatch.setenv("SELERIC_OPENAI_API_KEY", "sk-company")
    assert Settings(_env_file=None).openai_api_key == "sk-company"


# ---- Settings → Models store ---------------------------------------------------------------------------------------

from seleric_swarm.llm import models_store as store  # noqa: E402


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(store.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 443))])


def test_saved_keys_are_encrypted_and_only_the_last_four_are_shown(public_dns, tmp_path):
    row = store.add_connection("openrouter", "sk-or-secret-9876")
    raw = (tmp_path / "models.json").read_text()
    assert "sk-or-secret-9876" not in raw
    view = store.view(_settings())
    saved = next(c for c in view["connections"] if c["id"] == row["id"])
    assert saved["key_hint"] == "9876" and "secret" not in str(view)
    assert store.connections(_settings())[row["id"]].api_key == "sk-or-secret-9876"


def test_no_models_key_means_no_saving_and_saved_rows_are_skipped(public_dns, monkeypatch):
    row = store.add_connection("openai", "sk-1234")
    monkeypatch.delenv("SELERIC_MODELS_KEY")
    with pytest.raises(store.ModelsError):
        store.add_connection("openai", "sk-5678")
    assert row["id"] not in store.connections(_settings())


@pytest.mark.parametrize("url", ["http://api.example.com/v1", "https://localhost/v1", "ftp://x"])
def test_server_urls_must_be_public_https(url, monkeypatch):
    monkeypatch.setattr(store.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("127.0.0.1", 443))])
    with pytest.raises(store.ModelsError):
        store.check_base_url(url)


def test_private_address_behind_a_public_name_is_refused(monkeypatch):
    monkeypatch.setattr(store.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("10.0.0.5", 443))])
    with pytest.raises(store.ModelsError, match="private"):
        store.check_base_url("https://models.example.com/v1")


def test_saved_main_list_replaces_the_env_chain_and_runs_on_its_connection(public_dns):
    settings = _settings(azure_openai_models='["gpt-5-mini"]')
    assert store.role_chain(settings, "main") == [("azure", "gpt-5-mini", None)]  # no plan connected → env Azure
    row = store.add_connection("compatible", "sk-x", base_url="https://models.example.com/v1")
    roles = {"main": store.clean_role(settings, [{"connection": row["id"], "model": "m1", "thinking": "high"},
                                                 {"connection": "azure", "model": "gpt-4o-code"}]),
             "helper": []}
    store.save_roles(roles, expected_version=1)
    models = asyncio.run(_resolve(settings))
    assert [m.model_name for m in models] == ["m1", "gpt-4o-code"]
    assert _base(models[0]).startswith("https://models.example.com/v1")
    assert models[0].wrapped._settings["openai_reasoning_effort"] == "high"
    with pytest.raises(store.VersionConflict):
        store.save_roles(roles, expected_version=1)


def test_role_rows_are_validated():
    settings = _settings()
    with pytest.raises(store.ModelsError):
        store.clean_role(settings, [{"connection": "nope", "model": "x"}])
    with pytest.raises(store.ModelsError):
        store.clean_role(settings, [{"connection": "azure", "model": "a"}, {"connection": "azure", "model": "b", "thinking": "low"}])
    with pytest.raises(store.ModelsError):
        store.clean_role(settings, [{"connection": "azure", "model": "a"}] * (store.MAX_MODEL_BACKUPS + 2))


def test_disconnecting_drops_its_models_from_both_lists(public_dns):
    settings = _settings()
    row = store.add_connection("openai", "sk-abcd")
    store.save_roles({"main": [{"connection": row["id"], "model": "gpt-5-mini"}, {"connection": "azure", "model": "a"}],
                      "helper": [{"connection": row["id"], "model": "gpt-5-nano"}]}, None)
    store.remove_connection(row["id"])
    data = store.load()
    assert data["main"] == [{"connection": "azure", "model": "a"}] and data["helper"] == []


def test_api_connects_with_a_checked_key_and_never_returns_it(public_dns, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from seleric_swarm.api import model_settings as api

    async def fake_list(provider, base_url, api_key):
        if api_key != "sk-good-1111":
            raise store.ModelsError("The provider rejected this API key")
        return ["gpt-5-mini"]

    async def no_test(settings, row):
        return None

    monkeypatch.setattr(api, "_list_models", fake_list)
    monkeypatch.setattr(api, "_calls_tools", no_test)
    monkeypatch.setattr(api, "_settings", lambda request: _settings())
    from seleric_swarm.conversations.contracts import Principal

    app = FastAPI()

    @app.middleware("http")
    async def _signed_in(request, call_next):
        request.state.principal = Principal(principal_id="p", workspace_id="default", user_id="default", authenticated=True)
        return await call_next(request)

    app.include_router(api.router)
    client = TestClient(app)
    assert client.post("/v1/model-settings/connections", json={"provider": "openai", "api_key": "bad"}).status_code == 400
    ok = client.post("/v1/model-settings/connections", json={"provider": "openai", "api_key": "sk-good-1111"})
    assert ok.status_code == 200 and "sk-good" not in ok.text and ok.json()["models"] == ["gpt-5-mini"]
    conn = ok.json()["connection"]["id"]
    saved = client.put("/v1/model-settings/roles", json={"version": 1, "main": [{"connection": conn, "model": "gpt-5-mini"}]})
    assert saved.status_code == 200 and saved.json()["main"] == [{"connection": conn, "model": "gpt-5-mini"}]
    assert client.put("/v1/model-settings/roles", json={"version": 1, "main": []}).status_code == 409
    assert "sk-good" not in client.get("/v1/model-settings").text


def test_saved_models_this_container_cannot_decrypt_fall_back_to_the_server_chain(public_dns, monkeypatch):
    settings = _settings(azure_openai_models='["gpt-5-mini"]')
    row = store.add_connection("openrouter", "sk-or-1")
    store.save_roles({"main": [{"connection": row["id"], "model": "x"}], "helper": []}, None)
    monkeypatch.delenv("SELERIC_MODELS_KEY")
    models = asyncio.run(_resolve(settings))
    assert [m.model_name for m in models] == ["gpt-5-mini"]


def test_the_real_app_guards_model_settings_with_the_api_key(monkeypatch):
    from fastapi.testclient import TestClient

    from seleric_swarm import main
    from seleric_swarm.config.settings import get_settings

    monkeypatch.setenv("API_KEY", "real-key")
    get_settings.cache_clear()
    try:
        client = TestClient(main.app)
        assert client.get("/v1/model-settings").status_code == 401
        assert client.put("/v1/model-settings/roles", json={"main": []}).status_code == 401
        assert client.get("/v1/model-settings", headers={"x-api-key": "real-key"}).status_code == 200
    finally:
        get_settings.cache_clear()

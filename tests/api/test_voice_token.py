"""POST /v1/voice/token — the Phase 0 auth hop.

The properties under test are the ones that would otherwise fail silently:
voice must refuse the anonymous default principal, and must not mint a room
token for a thread in another workspace.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.conversations.contracts import Principal, PrincipalAuthMethod, Thread
from seleric_swarm.conversations.memory import build_in_memory_repositories
from seleric_swarm.voice import dev_page as _voice_dev_page  # noqa: F401
from seleric_swarm.voice import room_name_for_thread
from seleric_swarm.voice.token import router as voice_router

livekit_api = pytest.importorskip(
    "livekit.api", reason="voice token minting needs the 'voice-api' extra"
)

API_KEY = "test-voice-key"


class _Settings:
    voice_enabled = True
    livekit_url = "wss://example.livekit.cloud"
    livekit_api_key = "devkey"
    livekit_api_secret = "devsecret-at-least-32-chars-long-xxxx"
    app_env = "development"
    voice_token_ttl_s = 900

    def is_dev_surface(self) -> bool:
        return self.app_env in {"development", "dev", "test", "local"}

    def missing_voice_credentials(self) -> list[str]:
        if not self.voice_enabled:
            return []
        return [
            name
            for name, value in (
                ("LIVEKIT_URL", self.livekit_url),
                ("LIVEKIT_API_KEY", self.livekit_api_key),
                ("LIVEKIT_API_SECRET", self.livekit_api_secret),
            )
            if not value.strip()
        ]


class _Runtime:
    def __init__(self) -> None:
        self.settings = _Settings()
        self.conversations = build_in_memory_repositories()


def _build_client(runtime: _Runtime) -> TestClient:
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(voice_router)
    app.add_middleware(
        ApiSecurityMiddleware,
        api_key=API_KEY,
        rate_limit_enabled=False,
        default_workspace_id="default",
        default_user_id="default",
    )
    return TestClient(app)


@pytest.fixture
def runtime() -> _Runtime:
    return _Runtime()


@pytest.fixture
def client(runtime: _Runtime) -> TestClient:
    return _build_client(runtime)


def _auth() -> dict[str, str]:
    return {"x-api-key": API_KEY}


def test_mints_token_and_creates_thread(client: TestClient) -> None:
    response = client.post("/v1/voice/token", json={}, headers=_auth())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["url"] == _Settings.livekit_url
    assert body["room"] == room_name_for_thread(body["thread_id"])
    assert body["token"]
    assert body["expires_in_s"] == 900


def test_token_carries_principal_as_room_metadata(client: TestClient) -> None:
    """The worker reads identity from here; if it is absent or wrong, every
    voice user silently becomes the default principal (blocker B6)."""
    body = client.post("/v1/voice/token", json={}, headers=_auth()).json()

    claims = livekit_api.TokenVerifier(
        _Settings.livekit_api_key, _Settings.livekit_api_secret
    ).verify(body["token"])
    metadata = json.loads(claims.metadata)

    assert metadata["thread_id"] == body["thread_id"]
    assert metadata["workspace_id"]
    assert metadata["user_id"]


def test_token_grant_is_scoped_to_exactly_one_room(client: TestClient) -> None:
    body = client.post("/v1/voice/token", json={}, headers=_auth()).json()

    claims = livekit_api.TokenVerifier(
        _Settings.livekit_api_key, _Settings.livekit_api_secret
    ).verify(body["token"])

    assert claims.video.room == body["room"]
    assert claims.video.room_join is True
    # A participant must not be able to inject data messages into the room.
    assert not claims.video.can_publish_data


def test_unauthenticated_caller_is_refused(client: TestClient) -> None:
    """Without this, an anonymous caller gets a real audio channel scoped to
    the default workspace."""
    response = client.post("/v1/voice/token", json={})  # no x-api-key

    assert response.status_code == 401


def test_open_local_mode_authenticates_everyone(runtime: _Runtime) -> None:
    """Documents a real hole that voice inherits rather than creates.

    With no API_KEY configured, ApiSecurityMiddleware marks every caller
    authenticated as the default principal, so the 401 above does not fire.
    Production is safe because validate_for_startup() requires api_key, and
    Settings.validate_for_startup additionally refuses voice with fake
    STT/TTS. This test exists so that guarantee cannot regress silently.
    """
    app = FastAPI()
    app.state.runtime_provider = lambda: runtime
    app.include_router(voice_router)
    app.add_middleware(
        ApiSecurityMiddleware,
        api_key="",  # open local mode
        rate_limit_enabled=False,
        default_workspace_id="default",
        default_user_id="default",
    )

    response = TestClient(app).post("/v1/voice/token", json={})

    assert response.status_code == 201  # local convenience, not a voice bug

    from seleric_swarm.config.settings import Settings

    production = Settings(
        app_env="production",
        voice_enabled=True,
        livekit_url="wss://x",
        livekit_api_key="k",
        livekit_api_secret="s",
    )
    with pytest.raises(ValueError, match="api_key must be configured"):
        production.validate_for_startup()


def test_foreign_workspace_thread_is_not_found(
    client: TestClient, runtime: _Runtime
) -> None:
    """A thread id from another workspace must 404, not mint a token for it."""
    foreign = runtime.conversations.threads.create(
        Thread(workspace_id="other-workspace", owner_user_id="someone-else")
    )

    response = client.post(
        "/v1/voice/token", json={"thread_id": foreign.id}, headers=_auth()
    )

    assert response.status_code == 404


def test_reuses_an_owned_thread(client: TestClient) -> None:
    first = client.post("/v1/voice/token", json={}, headers=_auth()).json()

    second = client.post(
        "/v1/voice/token", json={"thread_id": first["thread_id"]}, headers=_auth()
    ).json()

    assert second["thread_id"] == first["thread_id"]
    assert second["room"] == first["room"]


def test_route_is_404_when_voice_is_disabled(runtime: _Runtime) -> None:
    runtime.settings.voice_enabled = False

    response = _build_client(runtime).post("/v1/voice/token", json={}, headers=_auth())

    assert response.status_code == 404


def test_missing_credentials_are_a_503_not_a_broken_token(runtime: _Runtime) -> None:
    """VOICE_ENABLED=true with empty credentials must fail loudly here rather
    than at Settings() construction, which would break unrelated processes."""
    runtime.settings.livekit_api_secret = ""

    response = _build_client(runtime).post("/v1/voice/token", json={}, headers=_auth())

    assert response.status_code == 503
    assert "LIVEKIT_API_SECRET" in response.json()["detail"]


def test_settings_construction_survives_voice_misconfiguration() -> None:
    """Guards the regression the first implementation had: a model validator
    made every Settings() raise, taking migrate/recover/tests down with it."""
    from seleric_swarm.config.settings import Settings

    # _env_file=None so a developer's real LiveKit values in .env cannot make
    # this pass for the wrong reason.
    settings = Settings(
        _env_file=None,
        voice_enabled=True,
        livekit_url="",
        livekit_api_key="",
        livekit_api_secret="",
    )

    assert settings.missing_voice_credentials() == [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ]


def test_production_startup_rejects_voice_without_credentials() -> None:
    from seleric_swarm.config.settings import Settings

    production = Settings(
        _env_file=None,
        app_env="production",
        voice_enabled=True,
        api_key="k",
        livekit_url="",
        livekit_api_key="",
        livekit_api_secret="",
    )

    with pytest.raises(ValueError, match="voice_enabled requires LIVEKIT_URL"):
        production.validate_for_startup()


def test_production_startup_rejects_fake_voice_providers() -> None:
    from seleric_swarm.config.settings import Settings

    production = Settings(
        app_env="production",
        voice_enabled=True,
        api_key="k",
        livekit_url="wss://x",
        livekit_api_key="k",
        livekit_api_secret="s",
        stt_provider="fake",
        tts_provider="fake",
    )

    with pytest.raises(ValueError, match="stt_provider=fake is not allowed"):
        production.validate_for_startup()


def test_room_name_is_deterministic_and_rejects_blank() -> None:
    assert room_name_for_thread("thr_1") == room_name_for_thread(" thr_1 ")
    with pytest.raises(ValueError):
        room_name_for_thread("   ")


def test_anonymous_principal_shape_is_what_the_route_refuses() -> None:
    """Guards the assumption behind test_unauthenticated_caller_is_refused."""
    principal = Principal(
        principal_id="anon", workspace_id="default", user_id="default"
    )
    assert principal.authenticated is False
    assert principal.auth_method is PrincipalAuthMethod.ANONYMOUS


def test_dev_page_is_accessible_without_api_key_header(client: TestClient) -> None:
    """The HTML page loads in browser without pre-request headers so the user can enter the key in the UI."""
    response = client.get("/v1/voice/dev")
    assert response.status_code == 200
    assert "Seleric Voice — dev console" in response.text


def test_format_spoken_summary_cleans_markdown() -> None:
    from seleric_swarm.voice.worker import format_spoken_summary

    raw_markdown = "### Quarterly Revenue\n\n**Revenue**: $4.2M (+12% YoY)\n**EBITDA**: $1.1M"
    spoken = format_spoken_summary(raw_markdown)
    assert "###" not in spoken
    assert "**" not in spoken
    assert "Revenue: $4.2M" in spoken


def test_principal_from_metadata_parses_valid_json() -> None:
    from seleric_swarm.voice.worker import principal_from_metadata

    raw = '{"workspace_id":"ws_1","user_id":"usr_1","thread_id":"thr_1"}'
    principal = principal_from_metadata(raw)
    assert principal is not None
    assert principal.workspace_id == "ws_1"
    assert principal.user_id == "usr_1"
    assert principal.thread_id == "thr_1"

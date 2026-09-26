from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from seleric_swarm.api.ready import check_readiness, effective_capabilities
from seleric_swarm.api.security import ApiSecurityMiddleware
from seleric_swarm.config.settings import Settings
from seleric_swarm.conversations.contracts import Principal, PrincipalAuthMethod


def _anonymous_admin() -> Principal:
    return Principal(
        principal_id="anonymous",
        workspace_id="workspace",
        user_id="user",
        authenticated=False,
        auth_method=PrincipalAuthMethod.ANONYMOUS,
        roles={"admin"},
    )


@pytest.mark.parametrize("provider_name", ["principal_provider", "authenticator"])
def test_false_authenticated_provider_cannot_bypass_protected_route(provider_name: str) -> None:
    app = FastAPI()

    @app.get("/v1/protected")
    def protected() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        ApiSecurityMiddleware,
        api_key="",
        rate_limit_enabled=False,
        **{provider_name: lambda _request: _anonymous_admin()},
    )
    assert TestClient(app).get("/v1/protected").status_code == 401


def test_anonymous_admin_role_is_not_privileged() -> None:
    principal = _anonymous_admin()
    assert principal.is_admin is False
    assert principal.is_internal is False


def test_production_empty_auth_refuses_startup() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        api_key="",
        llm_provider="fake",
        persistence_backend="memory",
    )
    with pytest.raises(ValueError, match="api_key must be configured"):
        settings.validate_for_startup()


def test_production_requires_minio_scanner_and_external_provider() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        api_key="secret",
        llm_provider="azure_openai_compatible",
        azure_openai_endpoint="https://llm.invalid",
        azure_openai_api_key="secret",
        azure_openai_model="model",
        persistence_backend="postgres",
        database_url="postgresql+psycopg://database",
        checkpoint_backend="postgres",
        cancellation_backend="redis",
        event_notifier_backend="redis",
        redis_url="redis://redis",
        seleric_mcp_url="",
        seleric_mcp_token="",
    )
    with pytest.raises(ValueError, match="blob_backend=minio.*ClamAV.*MCP URL"):
        settings.validate_for_startup()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_lease_s", 0, "run_lease_s must be positive"),
        ("run_heartbeat_s", 60, "run_heartbeat_s must be less than run_lease_s"),
        ("mission_timeout_s", 0, "mission_timeout_s must be positive"),
        ("run_max_attempts", 0, "run_max_attempts must be at least 1"),
        ("run_retry_delay_s", -1, "run_retry_delay_s must not be negative"),
        ("llm_max_retries", -1, "llm_max_retries must not be negative"),
    ],
)
def test_settings_reject_invalid_runtime_limits(field: str, value: int, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        # Pin run_lease_s=30 explicitly (unless the test is validating run_lease_s
        # itself) so that even when the developer's .env exports RUN_LEASE_S=300
        # into the OS environment (loaded by conftest), pydantic-settings doesn't
        # silently make heartbeat_s=60 < lease_s=300 valid.
        # With lease_s=30 the constraint heartbeat(60) >= lease(30) fires.
        extra = {} if field == "run_lease_s" else {"run_lease_s": 30}
        Settings(_env_file=None, **extra, **{field: value})


def test_readiness_degrades_when_database_probe_fails() -> None:
    class BrokenEngine:
        def connect(self):
            raise ConnectionError("database unavailable")

    settings = Settings(_env_file=None, app_env="test")
    runtime = SimpleNamespace(
        settings=settings,
        store=SimpleNamespace(_engine=BrokenEngine()),
        conversations=None,
        cancellation=None,
        activity_events=None,
        blob_store=SimpleNamespace(root=Path("."), scanner=None),
        run_queue=SimpleNamespace(enqueue=lambda _run_id: None),
        mcp=SimpleNamespace(capabilities=set()),
        action_execution=None,
    )
    payload = check_readiness(runtime, timeout_s=0.1)
    assert payload["ready"] is False
    assert payload["checks"]["database"]["ok"] is False


def test_capabilities_reflect_effective_adapters() -> None:
    runtime = SimpleNamespace(
        settings=Settings(_env_file=None, app_env="test", allow_write_actions=True),
        conversations=object(),
        action_execution=SimpleNamespace(executors={}),
        run_queue=None,
    )
    readiness = {
        "checks": {
            "database": {"ok": True},
            "queue": {"ok": False},
        }
    }
    capabilities = effective_capabilities(runtime, readiness)
    assert capabilities["hybrid_search"] is True
    assert capabilities["vector_search"] is False
    assert capabilities["action_execution"] is False
    assert capabilities["write_actions"] is False
    assert capabilities["scheduled_expiry"] is False


def test_ci_and_container_configuration_are_hardened() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'node-version: "22"' in workflow
    assert "uv sync --frozen --extra dev" in workflow
    assert "uv run mypy src" in workflow
    assert "npm run build" in workflow
    assert "LLM_PROVIDER=fake" not in dockerfile
    assert "PERSISTENCE_BACKEND=memory" not in dockerfile
    assert (
        sum(
            line.strip().startswith("MALWARE_SCANNER_BACKEND:")
            for line in compose.splitlines()
        )
        == 2
    )

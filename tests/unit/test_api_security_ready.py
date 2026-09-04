"""API security + readiness (v1.13)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

import seleric_swarm.main as main_mod
from seleric_swarm.api.ready import check_readiness
from seleric_swarm.api.security import ApiSecurityMiddleware, SlidingWindowRateLimiter
from seleric_swarm.main import app


def test_sliding_window_rate_limiter():
    lim = SlidingWindowRateLimiter(limit=3, window_s=60.0)
    assert lim.allow("a", now=1.0)[0] is True
    assert lim.allow("a", now=2.0)[0] is True
    assert lim.allow("a", now=3.0)[0] is True
    ok, remaining, retry = lim.allow("a", now=4.0)
    assert ok is False
    assert remaining == 0
    assert retry >= 1
    # other key unaffected
    assert lim.allow("b", now=4.0)[0] is True


def test_api_key_middleware_rejects_without_key():
    mini = FastAPI()

    @mini.get("/v1/secure")
    def secure():
        return {"ok": True}

    mini.add_middleware(ApiSecurityMiddleware, api_key="secret", rate_limit_enabled=False)
    client = TestClient(mini)
    assert client.get("/v1/secure").status_code == 401
    assert client.get("/v1/secure", headers={"X-API-Key": "secret"}).status_code == 200
    assert client.get("/v1/secure", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert client.get("/health").status_code == 404  # not registered on mini


def test_rate_limit_middleware_returns_429():
    mini = FastAPI()

    @mini.post("/v1/missions")
    def create():
        return PlainTextResponse("ok")

    mini.add_middleware(
        ApiSecurityMiddleware,
        api_key="",
        rate_limit_enabled=True,
        rate_limit_per_minute=2,
    )
    client = TestClient(mini)
    assert client.post("/v1/missions").status_code == 200
    assert client.post("/v1/missions").status_code == 200
    blocked = client.post("/v1/missions")
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Rate limit exceeded"
    assert "Retry-After" in blocked.headers


def test_readyz_and_health(runtime, monkeypatch):
    monkeypatch.setattr(main_mod, "_runtime", runtime)
    client = TestClient(app, raise_server_exceptions=True)
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/readyz")
    assert ready.status_code == 200
    body = ready.json()
    assert body["ready"] is True
    assert "store" in body["checks"]
    assert "mcp" in body["checks"]


def test_check_readiness_helper(runtime):
    payload = check_readiness(runtime)
    assert payload["ready"] is True
    assert payload["checks"]["mcp"]["ok"] is True

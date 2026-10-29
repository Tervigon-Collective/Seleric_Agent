"""Entra ID bearer-token authenticator."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from seleric_swarm.api.entra_auth import make_entra_authenticator
from seleric_swarm.api.security import ApiSecurityMiddleware

TENANT, AUD = "tenant-1", "api-client-id"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _client(monkeypatch, api_key: str = "") -> TestClient:
    authenticator = make_entra_authenticator(TENANT, AUD)
    monkeypatch.setattr(
        jwt.PyJWKClient,
        "get_signing_key_from_jwt",
        lambda self, token: SimpleNamespace(key=KEY.public_key()),
    )
    app = FastAPI()
    app.add_middleware(
        ApiSecurityMiddleware, api_key=api_key, rate_limit_enabled=False, authenticator=authenticator
    )

    @app.get("/v1/who")
    def who(request: Request):
        p = request.state.principal
        return {"user": p.user_id, "roles": sorted(p.roles), "method": p.auth_method}

    return TestClient(app)


def _token(**over) -> str:
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": AUD,
        "exp": int(time.time()) + 300,
        "oid": "user-oid",
        "roles": ["admin"],
    } | over
    return jwt.encode(claims, KEY, algorithm="RS256")


def _get(c: TestClient, token: str):
    return c.get("/v1/who", headers={"Authorization": f"Bearer {token}"})


def test_valid_token_becomes_principal(monkeypatch):
    r = _get(_client(monkeypatch), _token())
    assert r.status_code == 200
    assert r.json() == {"user": "user-oid", "roles": ["admin"], "method": "OIDC"}


def test_v1_issuer_accepted_wrong_audience_rejected(monkeypatch):
    c = _client(monkeypatch)
    assert _get(c, _token(iss=f"https://sts.windows.net/{TENANT}/")).status_code == 200
    assert _get(c, _token(aud="someone-else")).status_code == 401


def test_bad_tokens_rejected(monkeypatch):
    c = _client(monkeypatch)
    assert _get(c, _token(exp=int(time.time()) - 10)).status_code == 401
    assert _get(c, _token(iss="https://evil.example/")).status_code == 401
    assert _get(c, "not-a-jwt").status_code == 401


def test_api_key_still_works_alongside_oauth(monkeypatch):
    c = _client(monkeypatch, api_key="secret")
    assert _get(c, "secret").status_code == 200
    assert _get(c, _token()).status_code == 200
    assert _get(c, "wrong").status_code == 401

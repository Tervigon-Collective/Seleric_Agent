"""ChatGPT plan sign-in.

The browser returns to this site's ``/auth/callback`` (https://agent.seleric.com
when Models was opened there). The plan credential is encrypted in the models
file, and inference uses https://api.openai.com/v1/responses with that token.
This is not the Codex app's client.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from seleric_swarm.llm.models_store import ModelsError, add_oauth_connection, connections, replace_oauth_secret

AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
JWKS_URL = "https://auth.openai.com/.well-known/jwks.json"
ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
AGENT_NAME = "Seleric Agent"
CALLBACK_PATH = "/auth/callback"

_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}


def host_id(path) -> str:
    """Stable opaque host id, chosen before the first sign-in and reused."""
    file = path.with_name("chatgpt_host_id")
    try:
        saved = file.read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved.startswith("urn:uuid:"):
        return saved
    saved = f"urn:uuid:{uuid.uuid4()}"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(saved, encoding="utf-8")
    return saved


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(*, redirect: str, state: str, nonce: str, challenge: str, host: str, client_id: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "ext_agent_host_id": host,
    }
    if client_id == "dynamic_agent_client":
        params["agent_name_hint"] = AGENT_NAME
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def credential_from_token(body: dict[str, Any], claims: dict[str, Any], *, client_id: str) -> dict[str, Any]:
    scopes = str(body.get("scope") or "")
    if PLAN_SCOPE not in scopes.split():
        raise ModelsError("ChatGPT plan usage was not granted. Sign in again and allow plan usage.")
    access = str(body.get("access_token") or "")
    refresh = str(body.get("refresh_token") or "")
    if not access or not refresh:
        raise ModelsError("Sign-in did not return a usable token")
    expires_in = int(body.get("expires_in") or 3600)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "id_token": str(body.get("id_token") or ""),
        "expires_at": time.time() + expires_in,
        "client_id": client_id,
        "scope": scopes,
        "sub": str(claims.get("sub") or ""),
    }


def _validate_id_token(token: str, *, audience: str, nonce: str) -> dict[str, Any]:
    import jwt
    from jwt import PyJWKClient

    try:
        key = PyJWKClient(JWKS_URL).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=audience, issuer=ISSUER)
    except Exception as exc:  # noqa: BLE001 - the message shown to the operator must not include the token
        raise ModelsError("The ChatGPT sign-in could not be verified") from exc
    if claims.get("nonce") != nonce:
        raise ModelsError("The ChatGPT sign-in could not be verified")
    return claims


def _exchange(code: str, *, client_id: str, verifier: str, redirect: str) -> dict[str, Any]:
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect,
            "resource": RESOURCE,
        },
        timeout=20.0,
    )
    if response.status_code != 200:
        raise ModelsError("ChatGPT rejected the sign-in")
    try:
        return response.json()
    except ValueError as exc:
        raise ModelsError("ChatGPT rejected the sign-in") from exc


def callback_redirect(base: str) -> str:
    """The site the operator has open, plus ``/auth/callback``. ``https://agent.seleric.com/ui/`` stays on that host."""
    parts = urlsplit((base or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ModelsError("Could not tell which address to send ChatGPT back to")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}{CALLBACK_PATH}"


def _saved_client_id() -> str:
    from seleric_swarm.config.settings import Settings

    for conn in connections(Settings()).values():
        if conn.provider != "chatgpt" or not conn.api_key:
            continue
        try:
            client_id = json.loads(conn.api_key).get("client_id")
        except ValueError:
            continue
        if client_id and client_id != "dynamic_agent_client":
            return str(client_id)
    return "dynamic_agent_client"


def _page(message: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8><title>Seleric</title>"
        f"<p style=\"font-family:sans-serif\">{message}</p>"
    )


def begin_login(redirect_base: str) -> dict[str, str]:
    """Start a sign-in whose browser return address is this agent's callback."""
    from seleric_swarm.llm.models_store import _path

    redirect = callback_redirect(redirect_base)
    login_id = secrets.token_hex(8)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    client_id = _saved_client_id()
    holder: dict[str, Any] = {
        "login_id": login_id,
        "status": "pending",
        "error": None,
        "connection": None,
        "models": [],
        "verifier": verifier,
        "nonce": nonce,
        "client_id": client_id,
        "redirect": redirect,
        "created": time.time(),
    }
    url = authorize_url(
        redirect=redirect, state=state, nonce=nonce, challenge=challenge,
        host=host_id(_path()), client_id=client_id,
    )
    with _lock:
        _pending[state] = holder
    return {"login_id": login_id, "url": url}


def finish_login(query: dict[str, str]) -> str:
    """Browser landed on ``/auth/callback``. Returns a short HTML page and never echoes the code."""
    state = query.get("state") or ""
    with _lock:
        holder = _pending.get(state)
    if holder is None:
        return _page("This sign-in link is no longer valid. Close this tab and start again from Models.")
    if query.get("error"):
        holder["status"] = "error"
        holder["error"] = "ChatGPT plan usage was not granted"
        return _page("Plan usage was not granted. You can close this tab.")
    code = query.get("code") or ""
    client_id = str(holder["client_id"])
    issued = query.get("client_id") or client_id
    if client_id != "dynamic_agent_client" and issued != client_id:
        holder["status"] = "error"
        holder["error"] = "Sign-in returned a different client"
        return _page("Sign-in failed. You can close this tab.")
    if client_id == "dynamic_agent_client" and not issued.startswith("oai"):
        holder["status"] = "error"
        holder["error"] = "Sign-in did not finish registration"
        return _page("Sign-in failed. You can close this tab.")
    try:
        body = _exchange(code, client_id=issued, verifier=holder["verifier"], redirect=holder["redirect"])
        claims = _validate_id_token(str(body.get("id_token") or ""), audience=issued, nonce=holder["nonce"])
        secret = credential_from_token(body, claims, client_id=issued)
        row = add_oauth_connection("chatgpt", json.dumps(secret), hint="plan")
        holder["connection"] = {"id": row["id"], "provider": "chatgpt", "label": row["label"], "key_hint": row["key_hint"]}
        holder["models"] = list_models(secret["access_token"])
        holder["status"] = "connected"
    except ModelsError as exc:
        holder["status"] = "error"
        holder["error"] = str(exc)
        return _page("Sign-in failed. You can close this tab and try again from Models.")
    return _page("Signed in. You can close this tab and return to Seleric.")


def complete_from_address(login_id: str, pasted: str) -> dict[str, Any]:
    """Finish a sign-in from the address bar of the tab ChatGPT opened."""
    parts = urlsplit((pasted or "").strip())
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    with _lock:
        match = next(((state, row) for state, row in _pending.items() if row.get("login_id") == login_id), None)
    if match is None:
        raise ModelsError("That sign-in attempt has expired")
    expected, holder = match
    registered = urlsplit(str(holder.get("redirect") or ""))
    same = (
        parts.scheme == registered.scheme
        and parts.hostname == registered.hostname
        and parts.port == registered.port
        and parts.path == CALLBACK_PATH
    )
    if not same:
        raise ModelsError("Paste the full address from the ChatGPT tab")
    if query.get("state") != expected:
        raise ModelsError("That address is not for this sign-in")
    finish_login(query)
    result = login_status(login_id)
    if result["status"] == "error":
        raise ModelsError(str(result.get("error") or "Sign-in failed"))
    return result


def login_status(login_id: str) -> dict[str, Any]:
    with _lock:
        holder = next((row for row in _pending.values() if row.get("login_id") == login_id), None)
    if holder is None:
        raise ModelsError("That sign-in attempt has expired")
    if holder["status"] == "pending" and time.time() - float(holder["created"]) > 300:
        holder["status"] = "error"
        holder["error"] = "Sign-in timed out"
    out: dict[str, Any] = {"status": holder["status"]}
    if holder["status"] == "error":
        out["error"] = holder["error"] or "Sign-in failed"
    if holder["status"] == "connected":
        out["connection"] = holder["connection"]
        out["models"] = holder["models"]
    return out


def list_models(access_token: str) -> list[str]:
    response = httpx.get(f"{RESOURCE}/models", headers={"Authorization": f"Bearer {access_token}"}, timeout=15.0)
    if response.status_code in (401, 403):
        raise ModelsError("ChatGPT rejected this sign-in")
    if response.status_code != 200:
        raise ModelsError(f"ChatGPT answered {response.status_code} when listing models")
    try:
        body = response.json()
    except ValueError as exc:
        raise ModelsError("ChatGPT did not return a model list") from exc
    rows = body.get("models") or body.get("data") or []
    names = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("visibility") not in (None, "list"):
            continue
        name = row.get("slug") or row.get("id")
        if name:
            names.append(str(name).removeprefix("models/"))
    return names


def access_token(connection_id: str) -> str:
    """The current plan access token, refreshed when it is close to expiry."""
    from seleric_swarm.config.settings import Settings

    conn = connections(Settings()).get(connection_id)
    if conn is None or conn.provider != "chatgpt" or not conn.api_key:
        raise ModelsError("ChatGPT is not signed in")
    try:
        secret = json.loads(conn.api_key)
    except ValueError as exc:
        raise ModelsError("The saved ChatGPT sign-in is unreadable") from exc
    if float(secret.get("expires_at") or 0) - time.time() > 120:
        return str(secret["access_token"])
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": secret["client_id"],
            "refresh_token": secret["refresh_token"],
            "resource": RESOURCE,
        },
        timeout=20.0,
    )
    if response.status_code != 200:
        raise ModelsError("ChatGPT sign-in expired. Sign in again.")
    body = response.json()
    secret["access_token"] = body["access_token"]
    if body.get("refresh_token"):
        secret["refresh_token"] = body["refresh_token"]
    secret["expires_at"] = time.time() + int(body.get("expires_in") or 3600)
    replace_oauth_secret(connection_id, json.dumps(secret))
    return str(secret["access_token"])

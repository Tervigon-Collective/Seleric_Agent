"""Settings → Models (Rakazo-style): provider connections plus the Main and Helper model lists.

One JSON file next to the monitoring overrides, on the volume the api and recovery containers share, so a save in
the UI reaches the next mission without a restart. Keys are Fernet-encrypted with ``SELERIC_MODELS_KEY``; nothing
here ever returns a key, only its last four characters. API-key connections speak an OpenAI-compatible
endpoint. ChatGPT subscription keeps an encrypted plan sign-in. Claude subscription stores nothing: the
Claude Code CLI on this machine already holds that sign-in.

The server's own connections (Azure from env, OpenAI from ``SELERIC_OPENAI_API_KEY``) are always listed and cannot
be edited here. With no Main / Helper list saved, a connected subscription is the default and Azure from env is
the only fallback (``AZURE_OPENAI_MODELS`` / helper deployment). With neither, the env chain runs as before.
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from seleric_swarm.config.settings import Settings

# Offered under "Add provider". API-key rows speak an OpenAI-compatible endpoint.
# chatgpt is a ChatGPT plan sign-in (official Sign in with ChatGPT). claude-code runs the
# Claude Code CLI already signed in on this machine and stores no Claude token.
PROVIDERS: dict[str, dict[str, str]] = {
    "chatgpt": {"name": "ChatGPT subscription", "base_url": "https://api.openai.com/v1", "auth": "oauth"},
    "claude-code": {"name": "Claude subscription", "base_url": "", "auth": "cli"},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "auth": "key"},
    "anthropic": {"name": "Anthropic", "base_url": "https://api.anthropic.com/v1/", "auth": "key"},
    "google": {"name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "auth": "key"},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "auth": "key"},
    "compatible": {"name": "OpenAI-compatible server", "base_url": "", "auth": "key"},
}
MAX_MODEL_BACKUPS = 10  # Rakazo's limit: the main model plus ten fallbacks
THINKING_LEVELS = ("minimal", "low", "medium", "high")
ROLES = ("main", "helper")
SUBSCRIPTION_PROVIDERS = frozenset({"chatgpt", "claude-code"})
# Plan model ids when Settings → Models has no saved list. Prefer the strongest common plan models first.
_CHATGPT_DEFAULTS = {
    "main": ("gpt-6.2-astra", "gpt-6.1-terra", "gpt-6-astra", "gpt-5.6-terra"),
    "helper": ("gpt-6.2-sol", "gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol"),
}
_CLAUDE_DEFAULTS = {"main": "sonnet", "helper": "haiku"}
_lock = threading.Lock()


class ModelsError(ValueError):
    """Bad input; the message is safe to show (never contains a key)."""


class VersionConflict(ModelsError):
    pass


@dataclass(frozen=True)
class Connection:
    id: str
    provider: str
    label: str
    base_url: str | None  # None = the adapter's Azure settings
    api_key: str | None  # None = the adapter's own env key, or a CLI sign-in with no stored secret
    builtin: bool = False
    auth: str = "key"  # key | oauth | cli


def _path() -> Path:
    base = os.getenv("SELERIC_DOMAIN_HEALTH_DIR") or "var/domain_health_snapshots"
    return Path(os.getenv("SELERIC_MODELS_FILE") or Path(base) / "_config" / "models.json")


def _fernet() -> Any:
    raw = os.getenv("SELERIC_MODELS_KEY", "").strip()
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(raw.encode())
    except Exception:  # noqa: BLE001 - a missing package or bad key disables saved keys; it must never stop startup
        import logging

        logging.getLogger("seleric.models").error("SELERIC_MODELS_KEY unusable; saved providers are ignored")
        return None


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        import logging

        logging.getLogger("seleric.models").error("model settings unreadable at %s; using server defaults", _path())
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "version": int(data.get("version", 0)),
        "connections": list(data.get("connections") or []),
        "main": list(data.get("main") or []),
        "helper": list(data.get("helper") or []),
    }


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".models.json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _mutate(expected_version: int | None, change: Any) -> dict[str, Any]:
    with _lock:
        data = load()
        if expected_version is not None and expected_version != data["version"]:
            raise VersionConflict("Model settings were changed by someone else; reload and try again.")
        change(data)
        data["version"] += 1
        data["updated_at"] = datetime.now(UTC).isoformat()
        _write(data)
        return data


# ---- connections -------------------------------------------------------------------------------------------------


def builtin_connections(settings: Settings) -> list[Connection]:
    out = []
    if settings.provider_key("azure"):
        out.append(Connection("azure", "azure", "Azure (server)", None, None, builtin=True))
    if settings.provider_key("openai"):
        out.append(Connection("openai", "openai", "OpenAI (server)", settings.openai_base_url, settings.openai_api_key, True))
    return out


def connections(settings: Settings) -> dict[str, Connection]:
    found = {c.id: c for c in builtin_connections(settings)}
    fernet = _fernet()
    for row in load()["connections"]:
        auth = row.get("auth") or "key"
        if auth == "cli":  # the Claude Code CLI holds its own sign-in; this row has no secret
            found[row["id"]] = Connection(row["id"], row["provider"], row["label"], None, None, auth="cli")
            continue
        if fernet is None:
            continue  # a key row cannot be read; a later CLI sign-in still can
        try:
            key = fernet.decrypt(row["key_enc"].encode()).decode()
        except Exception:  # noqa: BLE001 - a rotated or wrong key must not take the agent down
            continue
        found[row["id"]] = Connection(row["id"], row["provider"], row["label"], row["base_url"], key, auth=auth)
    # Subscriptions first so UI pickers and "server default" start on the plan, not the Azure key.
    return dict(sorted(found.items(), key=lambda item: (0 if item[1].provider in SUBSCRIPTION_PROVIDERS else 1, item[0])))


def check_base_url(url: str) -> str:
    """A saved URL receives business data and the key: https only, and it must resolve to public addresses."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ModelsError("The server URL must start with https://")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ModelsError(f"Cannot resolve {parts.hostname}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ModelsError(f"{parts.hostname} resolves to a private address; only public servers are allowed")
    # shortcut: checked on save only (DNS could change later); re-check per call if non-admins ever edit models.
    return url


def add_connection(provider: str, api_key: str, base_url: str = "", label: str = "") -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ModelsError(f"Unknown provider {provider!r}")
    if PROVIDERS[provider].get("auth", "key") != "key":
        raise ModelsError(f"{PROVIDERS[provider]['name']} uses sign-in, not an API key")
    fernet = _fernet()
    if fernet is None:
        raise ModelsError("Saving keys needs SELERIC_MODELS_KEY on the server")
    api_key = (api_key or "").strip()
    if not api_key:
        raise ModelsError("Paste an API key")
    base_url = check_base_url(base_url or PROVIDERS[provider]["base_url"])
    row = {
        "id": f"c_{secrets.token_hex(4)}",
        "provider": provider,
        "label": (label or "").strip() or PROVIDERS[provider]["name"],
        "base_url": base_url,
        "key_enc": fernet.encrypt(api_key.encode()).decode(),
        "key_hint": api_key[-4:],
    }
    _mutate(None, lambda data: data["connections"].append(row))
    return row


def _require_fernet() -> Any:
    fernet = _fernet()
    if fernet is None:
        raise ModelsError("Saving keys needs SELERIC_MODELS_KEY on the server")
    return fernet


def add_oauth_connection(provider: str, secret: str, *, label: str = "", hint: str = "") -> dict[str, Any]:
    """Store a ChatGPT plan credential. ``secret`` is the encrypted JSON blob's plaintext."""
    if PROVIDERS.get(provider, {}).get("auth") != "oauth":
        raise ModelsError(f"{provider!r} is not a sign-in provider")
    fernet = _require_fernet()
    secret = (secret or "").strip()
    if not secret:
        raise ModelsError("Sign-in did not return a credential")
    row = {
        "id": f"c_{secrets.token_hex(4)}",
        "provider": provider,
        "auth": "oauth",
        "label": (label or "").strip() or PROVIDERS[provider]["name"],
        "base_url": PROVIDERS[provider]["base_url"],
        "key_enc": fernet.encrypt(secret.encode()).decode(),
        "key_hint": (hint or "plan")[-4:],
    }
    _mutate(None, lambda data: data["connections"].append(row))
    return row


def replace_oauth_secret(connection_id: str, secret: str) -> None:
    """Write a refreshed ChatGPT credential back. The refresh token rotates, so this must not race."""
    fernet = _require_fernet()
    encoded = fernet.encrypt(secret.encode()).decode()

    def change(data: dict[str, Any]) -> None:
        for row in data["connections"]:
            if row["id"] == connection_id and row.get("auth") == "oauth":
                row["key_enc"] = encoded
                return
        raise ModelsError("Unknown sign-in")

    _mutate(None, change)


def add_cli_connection(provider: str, *, label: str = "", hint: str = "") -> dict[str, Any]:
    """A Claude Code sign-in that already exists on this machine. No token is stored."""
    if PROVIDERS.get(provider, {}).get("auth") != "cli":
        raise ModelsError(f"{provider!r} is not a local CLI sign-in")
    row = {
        "id": f"c_{secrets.token_hex(4)}",
        "provider": provider,
        "auth": "cli",
        "label": (label or "").strip() or PROVIDERS[provider]["name"],
        "base_url": "",
        "key_enc": "",
        "key_hint": (hint or "cli")[:12],
    }
    _mutate(None, lambda data: data["connections"].append(row))
    return row


def remove_connection(connection_id: str) -> None:
    def change(data: dict[str, Any]) -> None:
        before = len(data["connections"])
        data["connections"] = [c for c in data["connections"] if c["id"] != connection_id]
        if len(data["connections"]) == before:
            raise ModelsError("Unknown connection")
        for role in ROLES:  # a model on a removed connection cannot run
            data[role] = [m for m in data[role] if m["connection"] != connection_id]

    _mutate(None, change)


# ---- roles -------------------------------------------------------------------------------------------------------


def clean_role(settings: Settings, rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) > MAX_MODEL_BACKUPS + 1:
        raise ModelsError(f"Up to {MAX_MODEL_BACKUPS} fallbacks")
    known = connections(settings)
    out = []
    for i, row in enumerate(rows):
        conn, model = str(row.get("connection", "")), str(row.get("model", "")).strip()
        if conn not in known or not model:
            raise ModelsError(f"Row {i + 1}: pick a connected provider and a model")
        thinking = row.get("thinking") or None
        if thinking is not None and (i > 0 or thinking not in THINKING_LEVELS):
            raise ModelsError("Thinking is set on the first model only, as minimal, low, medium or high")
        out.append({"connection": conn, "model": model, **({"thinking": thinking} if thinking else {})})
    return out


def save_roles(roles: dict[str, list[dict[str, Any]]], expected_version: int | None) -> dict[str, Any]:
    return _mutate(expected_version, lambda data: data.update(roles))


def _subscription_default_model(provider: str, role: str) -> str | None:
    if provider == "chatgpt":
        return _CHATGPT_DEFAULTS.get(role, ())[0] if _CHATGPT_DEFAULTS.get(role) else None
    if provider == "claude-code":
        return _CLAUDE_DEFAULTS.get(role)
    return None


def _subscription_defaults(known: dict[str, Connection], role: str) -> list[tuple[str, str, str | None]]:
    """One entry per connected plan, in connection order (already subscription-first)."""
    out: list[tuple[str, str, str | None]] = []
    for conn in known.values():
        if conn.provider not in SUBSCRIPTION_PROVIDERS:
            continue
        model = _subscription_default_model(conn.provider, role)
        if model:
            out.append((conn.id, model, None))
    return out


def _azure_fallback(settings: Settings, role: str, known: dict[str, Connection]) -> list[tuple[str, str, str | None]]:
    """Azure API-key deployments only — never the company OpenAI key — behind the plan."""
    if "azure" not in known:
        return []
    if role == "main":
        return [("azure", model, None) for model in settings.resolved_models()]
    from seleric_swarm.config.settings import helper_chat_model

    model = helper_chat_model(settings) or settings.primary_model()
    return [("azure", model, None)] if model else []


def default_role_chain(settings: Settings, role: str) -> list[tuple[str, str, str | None]]:
    """Server default for a role: connected subscriptions first, Azure API key last; else the env chain."""
    known = connections(settings)
    plans = _subscription_defaults(known, role)
    if plans:
        return plans + _azure_fallback(settings, role, known)
    env = settings.loop_chain() if role == "main" else settings.helper_chain()
    return [(conn, model, None) for conn, model in env if conn in known]


def role_chain(settings: Settings, role: str) -> list[tuple[str, str, str | None]]:
    """(connection id, model, thinking) for a role: the saved list, else subscription → Azure defaults.

    Entries whose connection is unknown here are dropped; if none is left (this container lacks SELERIC_MODELS_KEY,
    or the key was rotated) the default chain runs and the error is logged, so a mission never gets the stub model.
    """
    known = connections(settings)
    saved = [(m["connection"], m["model"], m.get("thinking")) for m in load()[role]]
    usable = [row for row in saved if row[0] in known]
    if saved and not usable:
        import logging

        logging.getLogger("seleric.models").error("saved %s models have no usable connection here; using server defaults", role)
    if usable:
        return usable
    return default_role_chain(settings, role)


def view(settings: Settings) -> dict[str, Any]:
    data = load()
    hints = {c["id"]: c.get("key_hint", "") for c in data["connections"]}
    defaults = {role: [{"connection": c, "model": m} for c, m, _ in default_role_chain(settings, role)] for role in ROLES}
    return {
        "version": data["version"],
        "can_save_keys": _fernet() is not None,
        "providers": [{"id": k, **v} for k, v in PROVIDERS.items()],
        "connections": [
            {"id": c.id, "provider": c.provider, "label": c.label, "base_url": c.base_url or "", "builtin": c.builtin,
             "auth": c.auth, "key_hint": hints.get(c.id, "")}
            for c in connections(settings).values()
        ],
        "main": data["main"],
        "helper": data["helper"],
        "defaults": defaults,
        "max_fallbacks": MAX_MODEL_BACKUPS,
        "thinking_levels": list(THINKING_LEVELS),
    }

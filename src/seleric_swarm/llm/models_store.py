"""Settings → Models (Rakazo-style): provider connections plus the Main and Helper model lists.

One JSON file next to the monitoring overrides, on the volume the api and recovery containers share, so a save in
the UI reaches the next mission without a restart. Keys are Fernet-encrypted with ``SELERIC_MODELS_KEY``; nothing
here ever returns a key, only its last four characters. Every connection speaks the OpenAI wire protocol (each
provider below serves an OpenAI-compatible endpoint), so the agent needs one client type: base URL + key.

The server's own connections (Azure from env, OpenAI from ``SELERIC_OPENAI_API_KEY``) are always listed and cannot
be edited here. With no Main model saved, the agent keeps the env chain (``LLM_MODELS`` / ``AZURE_OPENAI_MODELS``).
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

# Offered under "Add provider". Each is the vendor's OpenAI-compatible endpoint.
PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    "anthropic": {"name": "Anthropic", "base_url": "https://api.anthropic.com/v1/"},
    "google": {"name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
    "compatible": {"name": "OpenAI-compatible server", "base_url": ""},
}
MAX_MODEL_BACKUPS = 10  # Rakazo's limit: the main model plus ten fallbacks
THINKING_LEVELS = ("minimal", "low", "medium", "high")
ROLES = ("main", "helper")
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
    api_key: str | None  # None = the adapter's own env key
    builtin: bool = False


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
        if fernet is None:
            break  # keys cannot be read without SELERIC_MODELS_KEY: saved connections are skipped, not half-used
        try:
            key = fernet.decrypt(row["key_enc"].encode()).decode()
        except Exception:  # noqa: BLE001 - a rotated or wrong key must not take the agent down
            continue
        found[row["id"]] = Connection(row["id"], row["provider"], row["label"], row["base_url"], key)
    return found


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


def role_chain(settings: Settings, role: str) -> list[tuple[str, str, str | None]]:
    """(connection id, model, thinking) for a role: the saved list, else the server env chain.

    Entries whose connection is unknown here are dropped; if none is left (this container lacks SELERIC_MODELS_KEY,
    or the key was rotated) the env chain runs and the error is logged, so a mission never gets the stub model.
    """
    known = connections(settings)
    saved = [(m["connection"], m["model"], m.get("thinking")) for m in load()[role]]
    usable = [row for row in saved if row[0] in known]
    if saved and not usable:
        import logging

        logging.getLogger("seleric.models").error("saved %s models have no usable connection here; using server defaults", role)
    if usable:
        return usable
    env = settings.loop_chain() if role == "main" else settings.helper_chain()
    return [(conn, model, None) for conn, model in env if conn in known]


def view(settings: Settings) -> dict[str, Any]:
    data = load()
    hints = {c["id"]: c.get("key_hint", "") for c in data["connections"]}
    return {
        "version": data["version"],
        "can_save_keys": _fernet() is not None,
        "providers": [{"id": k, **v} for k, v in PROVIDERS.items()],
        "connections": [
            {"id": c.id, "provider": c.provider, "label": c.label, "base_url": c.base_url or "", "builtin": c.builtin,
             "key_hint": hints.get(c.id, "")}
            for c in connections(settings).values()
        ],
        "main": data["main"],
        "helper": data["helper"],
        "defaults": {
            "main": [{"connection": c, "model": m} for c, m in settings.loop_chain()],
            "helper": [{"connection": c, "model": m} for c, m in settings.helper_chain()],
        },
        "max_fallbacks": MAX_MODEL_BACKUPS,
        "thinking_levels": list(THINKING_LEVELS),
    }

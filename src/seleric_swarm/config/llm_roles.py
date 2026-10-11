"""LLM roles and the versioned prompt registry (M5.1).

``config/llm_roles.yaml`` gives each role (understand, answer, select, tool_loop) its deployment,
reasoning effort, timeout and prompt version. Prompts live in ``config/prompts/<role>/<version>.md``;
the version used is recorded in stage stats so eval reports can compare prompt versions.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from seleric_swarm.paths import repo_root

_log = logging.getLogger("seleric.llm_roles")


@dataclass(frozen=True)
class RoleConfig:
    role: str
    deployment: str = ""
    reasoning_effort: str = ""
    timeout_s: float | None = None
    prompt: str = ""


def _root() -> Path:
    return repo_root() / "config"


@functools.cache
def load_roles(path: str | None = None) -> dict[str, RoleConfig]:
    p = Path(path) if path else _root() / "llm_roles.yaml"
    try:
        raw = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("roles") or {}
    except (OSError, yaml.YAMLError):
        _log.warning("llm_roles_unreadable", exc_info=True)
        return {}
    out: dict[str, RoleConfig] = {}
    for role, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        timeout = cfg.get("timeout_s")
        out[str(role)] = RoleConfig(
            role=str(role),
            deployment=str(cfg.get("deployment") or ""),
            reasoning_effort=str(cfg.get("reasoning_effort") or ""),
            timeout_s=float(timeout) if isinstance(timeout, (int, float)) else None,
            prompt=str(cfg.get("prompt") or ""),
        )
    return out


def role(role_name: str) -> RoleConfig:
    cfg = load_roles().get(role_name) or RoleConfig(role=role_name)
    env = os.getenv(f"AZURE_OPENAI_{role_name.upper()}_MODEL", "").strip()
    return RoleConfig(cfg.role, env or cfg.deployment, cfg.reasoning_effort, cfg.timeout_s, cfg.prompt)


@functools.cache
def load_prompt(role_name: str, version: str = "") -> tuple[str | None, str]:
    """``(text, version)``; ``(None, "")`` when the role has no prompt file (callers use built-ins)."""
    folder = _root() / "prompts" / role_name
    version = version or role(role_name).prompt
    try:
        if not version:
            files = sorted(folder.glob("v*.md"), key=lambda f: (len(f.stem), f.stem))
            if not files:
                return None, ""
            version = files[-1].stem
        text = (folder / f"{version}.md").read_text(encoding="utf-8").strip()
    except OSError:
        return None, ""
    return (text or None), version

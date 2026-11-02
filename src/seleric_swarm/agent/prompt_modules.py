"""Loads the agent's prompt modules (markdown under ``prompt_library/``) so behaviour, query-type playbooks and per-domain knowledge live apart.

behaviour/   who the agent is and how it works and answers (static, domain-free)
playbooks/   one file per query type, including cross-domain ones (static; filename order is prompt order)
domains/     one file per Core module; ``views:`` frontmatter gates it on the catalogue having a metric in one of those views
Read once at import. A missing file raises at startup, never as a silently thinner prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources

import yaml

_ROOT = resources.files("seleric_swarm.agent") / "prompt_library"


@dataclass(frozen=True)
class Module:
    name: str
    body: str
    views: frozenset[str] = frozenset()


def _parse(name: str, raw: str) -> Module:
    meta: dict = {}
    if raw.startswith("---\n"):
        head, _, raw = raw[4:].partition("\n---\n")
        meta = yaml.safe_load(head) or {}
    return Module(name, raw.strip(), frozenset(meta.get("views") or ()))


@cache
def folder(sub: str) -> tuple[Module, ...]:
    files = sorted((p for p in (_ROOT / sub).iterdir() if p.name.endswith(".md")), key=lambda p: p.name)
    return tuple(_parse(p.name[:-3], p.read_text(encoding="utf-8")) for p in files)


def one(sub: str, name: str) -> str:
    return next(m.body for m in folder(sub) if m.name == name)


def domains_for(views: set[str]) -> str:
    """The domain modules whose views the catalogue serves, in filename order (so the text is stable per deployment)."""
    return "\n\n".join(m.body for m in folder("domains") if m.views & views)

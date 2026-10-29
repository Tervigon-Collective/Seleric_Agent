"""Recipient resolution: an explicit address or a trusted contact, otherwise a question. Never a guess."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


@dataclass(frozen=True)
class Contact:
    name: str
    email: str


@dataclass(frozen=True)
class Resolved:
    addresses: list[str]


@dataclass(frozen=True)
class Clarify:
    reason: str
    candidates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContactBook:
    contacts: list[Contact] = field(default_factory=list)
    groups: dict[str, list[str]] = field(default_factory=dict)
    allowed_domains: tuple[str, ...] = ()  # explicit addresses must be on one of these (or be a contact); () = no limit

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ContactBook:
        return cls([Contact(c["name"], c["email"].lower()) for c in raw.get("contacts") or []],
                   {k.lower(): [a.lower() for a in v] for k, v in (raw.get("groups") or {}).items()},
                   tuple(d.lower().lstrip("@") for d in raw.get("allowed_domains") or ()))

    @classmethod
    def load(cls, path: Path) -> ContactBook:
        return cls.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def resolve(ref: str, book: ContactBook) -> Resolved | Clarify:
    ref = ref.strip()
    if "@" in ref:
        if not _ADDRESS.match(ref):
            return Clarify(f"'{ref}' is not a valid email address.")
        address = ref.lower()
        trusted = address in {c.email for c in book.contacts} or any(address in m for m in book.groups.values())
        if book.allowed_domains and not trusted and address.rsplit("@", 1)[1] not in book.allowed_domains:
            return Clarify(f"'{address}' is not a trusted contact or on an allowed domain ({', '.join(book.allowed_domains)}). "
                           "Add them to the email contacts to send to them.")
        return Resolved([address])
    key = re.sub(r"^(the|my)\s+|\s+(team|group)$", "", ref.lower()).strip()
    if key in book.groups:
        return Resolved(list(book.groups[key]))
    hits = [c for c in book.contacts if key == c.name.lower() or key in c.name.lower().split()]
    if len(hits) == 1:
        return Resolved([hits[0].email])
    options = [f"{c.name} <{c.email}>" for c in hits]
    return Clarify(f"'{ref}' matches {len(hits)} contacts." if hits else f"No contact or group named '{ref}'.", options)

"""The process's mail wiring: the shared record store, the Graph sender and the trusted contacts.

``get_mail_runtime()`` is None until the sender is configured (tenant, client, secret and mailbox), so the agent says
email is not set up instead of drafting something it can never send."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from seleric_swarm.config.settings import get_settings
from seleric_swarm.mail.graph import GraphTransport
from seleric_swarm.mail.recipients import ContactBook
from seleric_swarm.mail.send import InMemoryStore, MailService, RecordStore, RedisStore
from seleric_swarm.paths import repo_root

_log = logging.getLogger("seleric.mail")


@dataclass
class MailRuntime:
    service: MailService
    sender: str
    contacts: ContactBook


_runtime: MailRuntime | None = None
_disabled = False


def set_mail_runtime(runtime: MailRuntime | None) -> None:
    """Install a runtime (tests, app start-up), or None to go back to building one from the settings."""
    global _runtime, _disabled
    _runtime, _disabled = runtime, False


def disable_mail() -> None:
    """No mail at all, whatever the settings say (tests must never build a real sender from a developer's .env)."""
    global _runtime, _disabled
    _runtime, _disabled = None, True


def _store(redis_url: str) -> RecordStore:
    if not redis_url.strip():
        return InMemoryStore()  # one process only: fine locally, not across the api and recovery containers
    import redis

    return RedisStore(redis.Redis.from_url(redis_url))


def get_mail_runtime() -> MailRuntime | None:
    global _runtime
    if _runtime is not None:
        return _runtime
    if _disabled:
        return None
    s = get_settings()
    if not all((s.azure_tenant_id, s.azure_client_id, s.azure_client_secret, s.email_sender)):
        return None
    path = Path(s.email_contacts_path)
    domains = tuple(d.strip() for d in s.email_allowed_domains.split(",") if d.strip())
    contacts = replace(ContactBook.load(path if path.is_absolute() else repo_root() / path), allowed_domains=domains)
    _runtime = MailRuntime(MailService(GraphTransport(s.azure_tenant_id, s.azure_client_id, s.azure_client_secret),
                                       _store(s.redis_url)), s.email_sender, contacts)
    return _runtime

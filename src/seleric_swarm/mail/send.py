"""Approve-then-send, with the guarantees that make sending safe.

* Approval is bound to the exact content (a hash of sender, recipients, subject, body and attachments) of one version:
  any edit makes a new version and voids it, and an approval of an older version is refused.
* A draft goes to the transport at most once: ``send`` marks it SENDING before it awaits, so retries and concurrent
  calls return the current record instead of sending again. A definite refusal (FAILED) may be retried with the same
  approval; an unknown outcome (a timeout: it may have gone out) is never retried by itself.
* SUBMITTED means the provider accepted the message. It is not delivery.

Records live in a ``RecordStore`` shared by every process (the container that runs the mission drafts, the API
container approves): Redis in the deployment, a dict in tests and local runs. The claim on SENDING is a
compare-and-set, so two processes cannot both send.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol


class SendStatus(StrEnum):
    DRAFT = "draft"
    SENDING = "sending"
    SUBMITTED = "submitted"
    FAILED = "failed"
    UNKNOWN = "unknown"


class NotApproved(Exception):
    """The current version of this draft has not been approved."""


class StaleApproval(Exception):
    """An approval for a version that is no longer the current one."""


class Rejected(Exception):
    """The provider definitely refused the message: nothing was sent."""


class Aborted(Exception):
    """The run that asked to send was cancelled before the message was handed to the provider."""


@dataclass(frozen=True)
class Attachment:
    name: str
    content: bytes
    mime: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    cid: str = ""  # set for an image the HTML body shows inline


@dataclass(frozen=True)
class Draft:
    sender: str
    to: tuple[str, ...]
    subject: str
    html: str
    text: str
    cc: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()

    def fingerprint(self) -> str:
        body = {"sender": self.sender, "to": self.to, "cc": self.cc, "subject": self.subject, "html": self.html,
                "text": self.text, "attachments": [(a.name, a.mime, a.cid, hashlib.sha256(a.content).hexdigest())
                                                   for a in self.attachments]}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class SendReceipt:
    provider_message_id: str | None = None


class MailTransport(Protocol):
    async def send(self, draft: Draft) -> SendReceipt: ...


@dataclass(frozen=True)
class Record:
    id: str
    version: int
    draft: Draft
    status: SendStatus = SendStatus.DRAFT
    approved: str | None = None  # fingerprint of the approved content
    provider_message_id: str | None = None
    error: str = ""
    meta: dict[str, str] = field(default_factory=dict)  # who owns it: workspace_id, user_id, thread_id
    run_id: str = ""  # the run that produced this version (a version is "previewed" once that run has ended)

    def to_json(self) -> str:
        body = {k: v for k, v in self.__dict__.items() if k not in ("draft", "status")}
        body["status"] = self.status.value
        body["draft"] = {**self.draft.__dict__, "attachments": [
            {"name": a.name, "mime": a.mime, "cid": a.cid, "content": base64.b64encode(a.content).decode()}
            for a in self.draft.attachments]}
        return json.dumps(body, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> Record:
        body = json.loads(raw)
        d = body.pop("draft")
        draft = Draft(**{**d, "to": tuple(d["to"]), "cc": tuple(d["cc"]), "attachments": tuple(
            Attachment(a["name"], base64.b64decode(a["content"]), a["mime"], a.get("cid", "")) for a in d["attachments"])})
        return cls(**{**body, "draft": draft, "status": SendStatus(body["status"])})


class RecordStore(Protocol):
    def get(self, draft_id: str) -> Record | None: ...
    def put(self, rec: Record) -> None: ...
    def swap(self, expected: Record, new: Record) -> bool:
        """Replace ``expected`` by ``new`` only if the stored record is still ``expected``."""
    def latest_id(self, thread_id: str) -> str | None: ...
    def set_latest(self, thread_id: str, draft_id: str) -> None: ...


class InMemoryStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Record] = {}
        self._latest: dict[str, str] = {}
        self._lock = threading.Lock()

    def latest_id(self, thread_id: str) -> str | None:
        return self._latest.get(thread_id)

    def set_latest(self, thread_id: str, draft_id: str) -> None:
        self._latest[thread_id] = draft_id

    def get(self, draft_id: str) -> Record | None:
        return self._by_id.get(draft_id)

    def put(self, rec: Record) -> None:
        self._by_id[rec.id] = rec

    def swap(self, expected: Record, new: Record) -> bool:
        with self._lock:
            if self._by_id.get(expected.id) != expected:
                return False
            self._by_id[new.id] = new
            return True


_CAS = "if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3]) return 1 end return 0"


class RedisStore:
    def __init__(self, client: Any, *, ttl_s: int = 7 * 86400, prefix: str = "seleric:mail:") -> None:
        self._r, self._ttl, self._prefix = client, ttl_s, prefix

    def get(self, draft_id: str) -> Record | None:
        raw = self._r.get(self._prefix + draft_id)
        return Record.from_json(raw.decode() if isinstance(raw, bytes) else raw) if raw else None

    def put(self, rec: Record) -> None:
        self._r.set(self._prefix + rec.id, rec.to_json(), ex=self._ttl)

    def latest_id(self, thread_id: str) -> str | None:
        raw = self._r.get(f"{self._prefix}thread:{thread_id}")
        return (raw.decode() if isinstance(raw, bytes) else raw) or None

    def set_latest(self, thread_id: str, draft_id: str) -> None:
        self._r.set(f"{self._prefix}thread:{thread_id}", draft_id, ex=self._ttl)

    def swap(self, expected: Record, new: Record) -> bool:
        return bool(self._r.eval(_CAS, 1, self._prefix + expected.id, expected.to_json(), new.to_json(), self._ttl))


@dataclass
class MailService:
    transport: MailTransport
    store: RecordStore = field(default_factory=InMemoryStore)

    def create(self, draft: Draft, *, meta: dict[str, str] | None = None, run_id: str = "") -> Record:
        if not draft.to:
            raise ValueError("an email needs at least one recipient")
        rec = Record(uuid.uuid4().hex, 1, draft, meta=meta or {}, run_id=run_id)
        self.store.put(rec)
        if rec.meta.get("thread_id"):  # a later turn knows only the chat text, so "send it" means this thread's latest draft
            self.store.set_latest(rec.meta["thread_id"], rec.id)
        return rec

    def latest_for_thread(self, thread_id: str) -> Record | None:
        draft_id = self.store.latest_id(thread_id)
        return self.store.get(draft_id) if draft_id else None

    def get(self, draft_id: str) -> Record:
        rec = self.store.get(draft_id)
        if rec is None:
            raise KeyError(draft_id)
        return rec

    def revise(self, draft_id: str, *, run_id: str = "", **changes: Any) -> Record:
        rec = self.get(draft_id)
        if rec.status is not SendStatus.DRAFT and rec.status is not SendStatus.FAILED:
            raise ValueError(f"this email was already {rec.status.value}; draft a new one")
        draft = replace(rec.draft, **changes)
        if not draft.to:
            raise ValueError("an email needs at least one recipient")
        rec = replace(rec, version=rec.version + 1, draft=draft, status=SendStatus.DRAFT, approved=None, error="",
                      run_id=run_id)
        self.store.put(rec)
        return rec

    def approve(self, draft_id: str, version: int) -> Record:
        rec = self.get(draft_id)
        if version != rec.version:
            raise StaleApproval(f"approval is for version {version}; the current draft is version {rec.version}")
        rec = replace(rec, approved=rec.draft.fingerprint())
        self.store.put(rec)
        return rec

    async def send(self, draft_id: str, *, should_abort: Callable[[], bool] | None = None) -> Record:
        rec = self.get(draft_id)
        if rec.status in (SendStatus.SENDING, SendStatus.SUBMITTED, SendStatus.UNKNOWN):
            return rec
        if rec.approved != rec.draft.fingerprint():
            raise NotApproved("the current version of this email has not been approved")
        if should_abort is not None and should_abort():  # a cancelled run must not send
            raise Aborted("the run was cancelled before the email was sent")
        if not self.store.swap(rec, replace(rec, status=SendStatus.SENDING, error="")):  # the claim: one sender wins
            return self.get(draft_id)
        try:
            receipt = await self.transport.send(rec.draft)
            done = replace(rec, status=SendStatus.SUBMITTED, provider_message_id=receipt.provider_message_id)
        except Rejected as exc:
            done = replace(rec, status=SendStatus.FAILED, error=str(exc))
        except Exception as exc:
            done = replace(rec, status=SendStatus.UNKNOWN, error=f"{type(exc).__name__}: {exc}")
        self.store.put(done)
        return done

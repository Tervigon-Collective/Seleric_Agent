"""Send safety: only the approved version goes out, once; outcomes are reported as they are, not as hoped."""

from __future__ import annotations

import asyncio

import pytest

from seleric_swarm.mail.send import (
    Attachment,
    Draft,
    MailService,
    NotApproved,
    Rejected,
    SendReceipt,
    SendStatus,
    StaleApproval,
)


class FakeTransport:
    def __init__(self, *outcomes: object) -> None:
        self.sent: list[Draft] = []
        self._outcomes = list(outcomes)

    async def send(self, draft: Draft) -> SendReceipt:
        self.sent.append(draft)
        await asyncio.sleep(0)  # yield, so concurrent callers interleave
        out = self._outcomes.pop(0) if self._outcomes else SendReceipt("msg-1")
        if isinstance(out, BaseException):
            raise out
        return out  # type: ignore[return-value]


def _draft(**kw) -> Draft:
    base = {"sender": "agent@seleric.ai", "to": ("gaurav@seleric.ai",), "subject": "Hello", "html": "<p>Hi</p>", "text": "Hi"}
    return Draft(**{**base, **kw})


async def test_unapproved_draft_is_never_sent() -> None:
    t = FakeTransport()
    svc = MailService(t)
    rec = svc.create(_draft())
    assert rec.status is SendStatus.DRAFT
    with pytest.raises(NotApproved):
        await svc.send(rec.id)
    assert t.sent == []


async def test_approved_draft_is_sent_once_and_the_outcome_recorded() -> None:
    t = FakeTransport(SendReceipt("graph-42"))
    svc = MailService(t)
    rec = svc.create(_draft())
    svc.approve(rec.id, rec.version)
    out = await svc.send(rec.id)
    assert out.status is SendStatus.SUBMITTED and out.provider_message_id == "graph-42"
    assert t.sent == [rec.draft]


async def test_retry_and_concurrent_sends_do_not_duplicate() -> None:
    t = FakeTransport()
    svc = MailService(t)
    rec = svc.create(_draft())
    svc.approve(rec.id, rec.version)
    results = await asyncio.gather(svc.send(rec.id), svc.send(rec.id), svc.send(rec.id))
    await svc.send(rec.id)
    assert len(t.sent) == 1 and {r.status for r in results} <= {SendStatus.SENDING, SendStatus.SUBMITTED}


async def test_a_material_edit_voids_the_approval() -> None:
    t = FakeTransport()
    svc = MailService(t)
    rec = svc.create(_draft())
    svc.approve(rec.id, rec.version)
    rec2 = svc.revise(rec.id, to=("gaurav@seleric.ai", "other@seleric.ai"))
    assert rec2.version == rec.version + 1 and rec2.status is SendStatus.DRAFT
    with pytest.raises(NotApproved):
        await svc.send(rec.id)
    with pytest.raises(StaleApproval):  # a spoken "yes" to the old preview cannot approve the new one
        svc.approve(rec.id, rec.version)
    svc.approve(rec.id, rec2.version)
    await svc.send(rec.id)
    assert t.sent[0].to == ("gaurav@seleric.ai", "other@seleric.ai")


async def test_changing_an_attachment_is_a_material_edit() -> None:
    svc = MailService(FakeTransport())
    rec = svc.create(_draft(attachments=(Attachment("a.xlsx", b"one"),)))
    svc.approve(rec.id, rec.version)
    svc.revise(rec.id, attachments=(Attachment("a.xlsx", b"two"),))
    with pytest.raises(NotApproved):
        await svc.send(rec.id)


async def test_definite_failure_is_reported_and_can_be_retried_with_the_same_approval() -> None:
    t = FakeTransport(Rejected("403 Forbidden"))
    svc = MailService(t)
    rec = svc.create(_draft())
    svc.approve(rec.id, rec.version)
    failed = await svc.send(rec.id)
    assert failed.status is SendStatus.FAILED and "403" in failed.error
    ok = await svc.send(rec.id)
    assert ok.status is SendStatus.SUBMITTED and len(t.sent) == 2


async def test_unknown_outcome_is_not_retried_and_not_called_success() -> None:
    t = FakeTransport(TimeoutError("read timeout"))
    svc = MailService(t)
    rec = svc.create(_draft())
    svc.approve(rec.id, rec.version)
    out = await svc.send(rec.id)
    assert out.status is SendStatus.UNKNOWN and "timeout" in out.error
    again = await svc.send(rec.id)  # it may have gone out: sending again could double-send
    assert again.status is SendStatus.UNKNOWN and len(t.sent) == 1


async def test_a_submitted_email_cannot_be_revised() -> None:
    svc = MailService(FakeTransport())
    rec = svc.create(_draft())
    svc.approve(rec.id, rec.version)
    await svc.send(rec.id)
    with pytest.raises(ValueError, match="already"):
        svc.revise(rec.id, subject="Changed")


def test_an_email_without_recipients_cannot_be_drafted() -> None:
    with pytest.raises(ValueError, match="recipient"):
        MailService(FakeTransport()).create(_draft(to=()))


# ---- shared store: the run that drafts (recovery container) and the request that approves (api) are different processes

from seleric_swarm.mail.send import Aborted, InMemoryStore, Record, RedisStore


async def test_two_processes_sharing_a_store_send_once() -> None:
    store, t = InMemoryStore(), FakeTransport()
    run_side, api_side = MailService(t, store), MailService(t, store)
    rec = run_side.create(_draft(), meta={"user_id": "u1"}, run_id="run_1")
    api_side.approve(rec.id, rec.version)
    await asyncio.gather(api_side.send(rec.id), run_side.send(rec.id))
    assert len(t.sent) == 1 and run_side.get(rec.id).status is SendStatus.SUBMITTED


def test_a_version_remembers_the_run_that_produced_it() -> None:
    svc = MailService(FakeTransport())
    rec = svc.create(_draft(), run_id="run_1")
    assert svc.revise(rec.id, run_id="run_2", subject="New").run_id == "run_2" and rec.run_id == "run_1"


async def test_a_cancelled_run_does_not_send() -> None:
    t = FakeTransport()
    svc = MailService(t)
    rec = svc.create(_draft())
    svc.approve(rec.id, rec.version)
    with pytest.raises(Aborted):
        await svc.send(rec.id, should_abort=lambda: True)
    assert t.sent == [] and svc.get(rec.id).status is SendStatus.DRAFT
    assert (await svc.send(rec.id)).status is SendStatus.SUBMITTED  # the approval stands for a later, uncancelled send


def test_a_record_survives_json() -> None:
    rec = MailService(FakeTransport()).create(
        _draft(cc=("a@seleric.ai",), attachments=(Attachment("a.xlsx", b"\x00\xffbytes"),)), meta={"user_id": "u1"}, run_id="r")
    assert Record.from_json(rec.to_json()) == rec


class FakeRedis:
    """Just the commands RedisStore uses; eval runs the compare-and-set the Lua script does."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get(self, key: str) -> bytes | None:
        return self.kv[key].encode() if key in self.kv else None

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.kv[key] = value

    def eval(self, _script: str, _n: int, key: str, expected: str, new: str, _ttl: int) -> int:
        if self.kv.get(key) != expected:
            return 0
        self.kv[key] = new
        return 1


def test_redis_store_round_trips_and_swaps_only_from_the_expected_value() -> None:
    store = RedisStore(FakeRedis())
    rec = MailService(FakeTransport(), store).create(_draft(), run_id="r")
    assert store.get(rec.id) == rec and store.get("missing") is None
    sending = Record(**{**rec.__dict__, "status": SendStatus.SENDING})
    assert store.swap(rec, sending) is True
    assert store.swap(rec, sending) is False  # a second claimant loses
    assert store.get(rec.id).status is SendStatus.SENDING


@pytest.mark.integration
async def test_redis_store_against_a_real_redis() -> None:
    """Needs MAIL_TEST_REDIS_URL (a throwaway Redis): the Lua compare-and-set is only checked by a real server."""
    import os

    url = os.environ.get("MAIL_TEST_REDIS_URL")
    if not url:
        pytest.skip("MAIL_TEST_REDIS_URL not set")
    import redis

    client = redis.Redis.from_url(url)
    store = RedisStore(client, prefix="mailtest:")
    svc = MailService(FakeTransport(), store)
    rec = svc.create(_draft(attachments=(Attachment("a.xlsx", b"\x00\xff"),)), meta={"user_id": "u1"}, run_id="r")
    assert store.get(rec.id) == rec
    svc.approve(rec.id, rec.version)
    results = await asyncio.gather(*(MailService(FakeTransport(), RedisStore(redis.Redis.from_url(url), prefix="mailtest:"))
                                     .send(rec.id) for _ in range(5)))
    assert sorted(r.status.value for r in results).count("submitted") >= 1
    sent_by = [s for s in results if s.status is SendStatus.SUBMITTED]
    assert len(sent_by) == 1  # five "processes" raced for the same draft: exactly one claimed it
    client.delete("mailtest:" + rec.id)


# ---- "yes, send it": the next turn knows only text history, so the thread must remember its latest draft

def test_the_latest_draft_of_a_thread_is_remembered_in_both_stores() -> None:
    for store in (InMemoryStore(), RedisStore(FakeRedis())):
        svc = MailService(FakeTransport(), store)
        assert svc.latest_for_thread("t1") is None
        first = svc.create(_draft(), meta={"thread_id": "t1"}, run_id="r1")
        second = svc.create(_draft(subject="Other"), meta={"thread_id": "t1"}, run_id="r2")
        other = svc.create(_draft(), meta={"thread_id": "t2"}, run_id="r3")
        assert svc.latest_for_thread("t1").id == second.id and svc.latest_for_thread("t2").id == other.id
        assert first.id != second.id

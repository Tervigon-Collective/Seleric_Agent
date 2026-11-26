"""B20 delivery rule and the bot email (fake mail transport, offline)."""

from __future__ import annotations

import asyncio

import pytest

from seleric_swarm.mail.recipients import Contact, ContactBook
from seleric_swarm.mail.send import MailService, SendReceipt
from seleric_swarm.mail.session import MailRuntime, disable_mail, set_mail_runtime
from seleric_swarm.services.bots import delivery


@pytest.mark.parametrize("kind,outcome,prev,expected", [
    ("chat", "action_needed", None, None),              # the person asking already sees it
    ("manual", "action_needed", None, None),
    ("cron", "no_action", None, "scheduled summary"),   # daily summary
    ("signal", "action_needed", None, "action needed"),
    ("signal", "cleared", None, None),
    ("watch", "no_action", None, None),
    ("watch", "unverified", "no_action", None),         # one failed check is not news
    ("webhook", "unverified", "unverified", "could not check, twice in a row"),
    ("cron", "unverified", "unverified", "could not check, twice in a row"),
])
def test_b20_rule(kind, outcome, prev, expected):
    assert delivery.why_notify(kind, outcome, prev) == expected


class _Fake:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, draft):
        self.sent.append(draft)
        return SendReceipt("m1")


BOT = {"bot_id": "bot_1", "name": "Marketing", "notify": {"email": ["Asha", "nobody"]}}
RUN = {"run_id": "run_a", "trigger_kind": "signal", "reason": "critical finding F1"}
ANSWER = "Spend is 3x pace on Sale.\n\nOUTCOME: action_needed — Sale overspending"


def test_email_goes_to_resolved_recipients_without_the_outcome_line():
    fake = _Fake()
    book = ContactBook(contacts=[Contact(name="Asha", email="asha@example.com")])
    set_mail_runtime(MailRuntime(MailService(fake), "bots@example.com", book))
    try:
        rows = asyncio.run(delivery.deliver(BOT, RUN, "action_needed", "Sale overspending", ANSWER, None))
    finally:
        disable_mail()
    assert [r["tool"] for r in rows] == ["notification_center", "email"]
    assert rows[1]["outcome"] == "completed" and rows[1]["target"] == "asha@example.com"
    draft = fake.sent[0]
    assert draft.to == ("asha@example.com",) and draft.subject == "[Seleric bot] Marketing: action needed — Sale overspending"
    assert "OUTCOME:" not in draft.text and "Spend is 3x pace" in draft.text and "critical finding F1" in draft.text


def test_no_mail_server_is_a_visible_failed_row_and_no_recipients_is_no_row():
    disable_mail()
    rows = asyncio.run(delivery.deliver(BOT, RUN, "action_needed", "x", ANSWER, None))
    assert rows[1]["outcome"] == "failed" and "not set up" in rows[1]["summary"]
    rows = asyncio.run(delivery.deliver({**BOT, "notify": {}}, RUN, "action_needed", "x", ANSWER, None))
    assert [r["tool"] for r in rows] == ["notification_center"]
    assert asyncio.run(delivery.deliver(BOT, {**RUN, "trigger_kind": "chat"}, "action_needed", "x", ANSWER, None)) == []


def test_answer_table_becomes_an_email_table_in_place():
    body, found = delivery.tables("**All calm.**\n\n| Check | Now |\n|---|---|\n| Where today sits | ₹4,181 |\n\n## What to do\n\n- Nothing to change.")
    assert body == "**All calm.**\n\n[[table:answer1]]\n\n## What to do\n\n- Nothing to change."
    t = found["answer1"][0]
    assert t.headers == ["Check", "Now"] and t.rows == [["Where today sits", "₹4,181"]]

"""The Microsoft Graph transport (the ReportAutomation sender): token, MIME body, and how each status is classified."""

from __future__ import annotations

import base64
from email import message_from_bytes, policy

import httpx
import pytest

from seleric_swarm.mail.graph import GraphTransport
from seleric_swarm.mail.send import Attachment, Draft, Rejected

DRAFT = Draft(sender="agent@seleric.ai", to=("gaurav@seleric.ai",), cc=("ops@seleric.ai",), subject="Checkout ₹ drop",
              html="<p>Hi</p>", text="Hi", attachments=(Attachment("data.xlsx", b"PK-bytes"),))


def _transport(send_status: int = 202, token_status: int = 200):
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if "oauth2/v2.0/token" in req.url.path:
            return httpx.Response(token_status, json={"access_token": "tok"} if token_status == 200 else {"error": "bad"})
        return httpx.Response(send_status, text="")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GraphTransport("tenant-1", "client-1", "secret-1", client=client), calls


async def test_sends_mime_with_text_fallback_and_attachment_as_the_sender() -> None:
    t, calls = _transport()
    receipt = await t.send(DRAFT)
    token, send = calls
    assert token.url.path == "/tenant-1/oauth2/v2.0/token"
    assert b"grant_type=client_credentials" in token.content and b"client_id=client-1" in token.content
    assert send.url.path == "/v1.0/users/agent@seleric.ai/sendMail"
    assert send.headers["authorization"] == "Bearer tok" and send.headers["content-type"] == "text/plain"
    msg = message_from_bytes(base64.b64decode(send.content), policy=policy.default)
    assert msg["To"] == "gaurav@seleric.ai" and msg["Cc"] == "ops@seleric.ai" and msg["Subject"] == "Checkout ₹ drop"
    assert msg.get_body(("plain",)).get_content().strip() == "Hi"
    assert "<p>Hi</p>" in msg.get_body(("html",)).get_content()
    (att,) = list(msg.iter_attachments())
    assert att.get_filename() == "data.xlsx" and att.get_content() == b"PK-bytes"
    assert receipt.provider_message_id is None  # Graph's sendMail returns 202 with no message id


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
async def test_refusals_are_definite_failures(status: int) -> None:
    t, _ = _transport(send_status=status)
    with pytest.raises(Rejected, match=str(status)):
        await t.send(DRAFT)


async def test_a_server_error_is_not_called_a_refusal() -> None:
    t, _ = _transport(send_status=503)  # the message may or may not have been taken
    with pytest.raises(httpx.HTTPStatusError):
        await t.send(DRAFT)


async def test_token_failure_means_nothing_was_sent() -> None:
    t, calls = _transport(token_status=401)
    with pytest.raises(Rejected, match="token"):
        await t.send(DRAFT)
    assert len(calls) == 1


def _failing(exc_on: str, exc: Exception):
    def handler(req: httpx.Request) -> httpx.Response:
        if ("oauth2" in req.url.path) == (exc_on == "token"):
            raise exc
        return httpx.Response(200, json={"access_token": "tok"}) if "oauth2" in req.url.path else httpx.Response(202)

    return GraphTransport("t", "c", "s", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("where", ["token", "send"])
async def test_a_connection_that_never_opened_means_nothing_was_sent(where: str) -> None:
    with pytest.raises(Rejected, match="nothing was sent"):
        await _failing(where, httpx.ConnectError("refused")).send(DRAFT)


async def test_a_message_that_cannot_be_built_means_nothing_was_sent() -> None:
    bad = Draft(sender="agent@seleric.ai", to=("gaurav@seleric.ai",), subject="two\nlines", html="<p>x</p>", text="x")
    t, calls = _transport()
    with pytest.raises(Rejected, match="nothing was sent"):
        await t.send(bad)
    assert calls == []  # not even the token request


async def test_a_read_timeout_after_the_post_stays_unknown() -> None:
    with pytest.raises(httpx.ReadTimeout):  # the provider may have taken it
        await _failing("send", httpx.ReadTimeout("slow")).send(DRAFT)


async def test_text_parts_are_base64_so_a_rupee_sign_is_never_split_by_a_soft_line_break() -> None:
    long_line = "Net sales (event date) fell 10.8% week-over-week — ₹3,84,243 → ₹3,42,703 for 14–20 Oct vs 21–27 Oct. " * 3
    t, calls = _transport()
    await t.send(Draft(sender="agent@seleric.ai", to=("gaurav@seleric.ai",), subject="s", html=f"<p>{long_line}</p>", text=long_line))
    raw = base64.b64decode(calls[1].content).decode("ascii", "replace")
    assert raw.lower().count("content-transfer-encoding: base64") >= 2
    assert "quoted-printable" not in raw.lower()
    msg = message_from_bytes(base64.b64decode(calls[1].content), policy=policy.default)
    assert msg.get_body(("plain",)).get_content().strip() == long_line.strip()

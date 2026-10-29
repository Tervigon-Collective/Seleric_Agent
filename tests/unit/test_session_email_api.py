"""POST /v1/emails/{id}/approve: the card's button. Approves exactly the version shown, sends once, reports honestly."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from seleric_swarm.api.emails import router
from seleric_swarm.conversations.contracts import Principal
from seleric_swarm.mail.recipients import ContactBook
from seleric_swarm.mail.send import Draft, InMemoryStore, MailService, Rejected, SendReceipt
from seleric_swarm.mail.session import MailRuntime, disable_mail, set_mail_runtime


class Transport:
    def __init__(self, *outcomes: object) -> None:
        self.sent: list = []
        self.outcomes = list(outcomes)

    async def send(self, draft):
        self.sent.append(draft)
        out = self.outcomes.pop(0) if self.outcomes else SendReceipt("graph-1")
        if isinstance(out, BaseException):
            raise out
        return out


def _client(user: str = "u1") -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.principal = Principal(principal_id=user, workspace_id="ws1", user_id=user, authenticated=True)
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _setup(*outcomes: object):
    t = Transport(*outcomes)
    svc = MailService(t, InMemoryStore())
    set_mail_runtime(MailRuntime(svc, "agent@seleric.ai", ContactBook()))
    rec = svc.create(Draft("agent@seleric.ai", ("gaurav@seleric.ai",), "Hello", "<p>Hi</p>", "Hi"),
                     meta={"user_id": "u1", "workspace_id": "ws1"}, run_id="r1")
    return t, svc, rec


@pytest.fixture(autouse=True)
def _reset():
    yield
    disable_mail()


def test_approving_the_shown_version_sends_once_and_a_second_click_does_not_resend() -> None:
    t, _, rec = _setup()
    c = _client()
    first = c.post(f"/v1/emails/{rec.id}/approve", json={"version": 1})
    second = c.post(f"/v1/emails/{rec.id}/approve", json={"version": 1})
    assert first.status_code == 200 and first.json()["status"] == "submitted"
    assert second.status_code == 200 and second.json()["status"] == "submitted" and len(t.sent) == 1


def test_an_out_of_date_preview_is_a_readable_conflict() -> None:
    t, svc, rec = _setup()
    svc.revise(rec.id, run_id="r2", subject="Changed")
    r = _client().post(f"/v1/emails/{rec.id}/approve", json={"version": 1})
    assert r.status_code == 409 and "out of date" in r.json()["detail"] and t.sent == []


def test_a_draft_that_does_not_exist_or_is_not_yours_is_404() -> None:
    t, _, rec = _setup()
    assert _client().post("/v1/emails/nope/approve", json={"version": 1}).status_code == 404
    assert _client("intruder").post(f"/v1/emails/{rec.id}/approve", json={"version": 1}).status_code == 404
    assert _client("intruder").get(f"/v1/emails/{rec.id}").status_code == 404
    assert t.sent == []


@pytest.mark.parametrize(("outcome", "status"), [(Rejected("403"), "failed"), (TimeoutError("slow"), "unknown")])
def test_provider_trouble_is_reported_not_hidden(outcome: BaseException, status: str) -> None:
    _, _, rec = _setup(outcome)
    r = _client().post(f"/v1/emails/{rec.id}/approve", json={"version": 1})
    assert r.status_code == 200 and r.json()["status"] == status and r.json()["error"]


def test_status_can_be_read_back() -> None:
    _, _, rec = _setup()
    body = _client().get(f"/v1/emails/{rec.id}").json()
    assert body["status"] == "draft" and body["version"] == 1 and body["to"] == ["gaurav@seleric.ai"]


def test_without_a_sender_the_endpoint_is_unavailable() -> None:
    disable_mail()
    assert _client().post("/v1/emails/x/approve", json={"version": 1}).status_code == 503

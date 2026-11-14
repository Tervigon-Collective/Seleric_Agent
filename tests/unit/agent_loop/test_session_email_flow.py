"""End to end, locally: a scripted model drafts through the real loop, the preview reaches the stored message, and the
user's approval (the card's button, or a later chat message) sends exactly once."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.loop import run_turn
from seleric_swarm.api import conversations as conversations_api
from seleric_swarm.api.emails import router
from seleric_swarm.conversations.contracts import ContextBundle, MessagePartType, Principal
from seleric_swarm.mail.recipients import ContactBook
from seleric_swarm.mail.send import InMemoryStore, MailService, SendReceipt
from seleric_swarm.mail.session import MailRuntime, disable_mail, set_mail_runtime
from seleric_swarm.state.artifacts import InMemoryArtifactStore

QUESTION = "Net sales were ₹3,42,703 last week. Send this to gaurav."


class Transport:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, draft):
        self.sent.append(draft)
        return SendReceipt(None)


def _deps(store: InMemoryArtifactStore, run_id: str) -> SelericDeps:
    return SelericDeps(
        mission_id=f"m-{run_id}", as_of=datetime(2026, 10, 28, tzinfo=UTC),
        principal=Principal(principal_id="u1", workspace_id="ws1", user_id="u1", authenticated=True), thread_id="t1",
        run_id=run_id, trace_id="x", context=ContextBundle(), mcp_client=NullMcpClient(), artifact_store=store,
        limits=ExecutionLimits())


def _model(call: ToolCallPart | None, final: str, args_from_card: Any = None) -> FunctionModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returned = any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts)
        if call is None or returned:
            return ModelResponse(parts=[TextPart(final)])
        return ModelResponse(parts=[call])

    return FunctionModel(fn)


async def _turn(store, run_id: str, model: FunctionModel, question: str = QUESTION):
    return await run_turn(deps=_deps(store, run_id), model=model, gateway=NullMcpClient(), tool_defs=[], system_prompt="sys",
                          question=question, history=[])


def _draft_call() -> ToolCallPart:
    return ToolCallPart("draft_ad_hoc_email", {"to": ["gaurav"], "subject": "Net sales last week",
                                               "body": "Hi Gaurav,\n\nNet sales were ₹3,42,703.\n\nThanks"})


async def test_draft_then_approve_with_the_card_sends_exactly_once() -> None:
    t = Transport()
    set_mail_runtime(MailRuntime(MailService(t, InMemoryStore()), "agent@seleric.ai",
                                 ContactBook.from_dict({"contacts": [{"name": "Gaurav", "email": "gaurav@seleric.ai"}]})))
    try:
        store = InMemoryArtifactStore()
        result = await _turn(store, "r1", _model(_draft_call(), "Draft is ready: please approve it."))
        assert t.sent == []  # drafting never sends
        stored = conversations_api._answer_parts(result.answer, {"parts": result.state.parts})  # what the UI will load
        (card,) = [p.content for p in stored if p.type is MessagePartType.APPROVAL]
        assert card["to"] == ["gaurav@seleric.ai"] and card["subject"] == "Net sales last week"

        app = FastAPI()

        @app.middleware("http")
        async def principal(request: Request, call_next):
            request.state.principal = Principal(principal_id="u1", workspace_id="ws1", user_id="u1", authenticated=True)
            return await call_next(request)

        app.include_router(router)
        c = TestClient(app)
        for _ in range(2):  # a double click
            r = c.post(f"/v1/emails/{card['approval_id']}/approve", json={"version": card["version"]})
            assert r.status_code == 200 and r.json()["status"] == "submitted"
        assert len(t.sent) == 1 and t.sent[0].to == ("gaurav@seleric.ai",) and "₹3,42,703" in t.sent[0].text
    finally:
        disable_mail()


async def test_draft_then_a_later_chat_approval_sends_exactly_once() -> None:
    t = Transport()
    set_mail_runtime(MailRuntime(MailService(t, InMemoryStore()), "agent@seleric.ai",
                                 ContactBook.from_dict({"contacts": [{"name": "Gaurav", "email": "gaurav@seleric.ai"}]})))
    try:
        store = InMemoryArtifactStore()
        first = await _turn(store, "r1", _model(_draft_call(), "Draft is ready: please approve it."))
        assert [p for p in first.state.parts if p["type"] == "APPROVAL"]
        send = ToolCallPart("send_email", {})  # the next turn sees only chat text: no draft id, no version
        second = await _turn(store, "r2", _model(send, "Submitted."), question="yes, send it")
        assert len(t.sent) == 1 and not [p for p in second.state.parts if p["type"] == "APPROVAL"]
        replay = await _turn(store, "r3", _model(send, "Already done."), question="send it again")
        assert len(t.sent) == 1 and replay.answer == "Already done."
    finally:
        disable_mail()


async def test_drafting_and_sending_in_one_turn_sends_nothing() -> None:
    t = Transport()
    set_mail_runtime(MailRuntime(MailService(t, InMemoryStore()), "agent@seleric.ai",
                                 ContactBook.from_dict({"contacts": [{"name": "Gaurav", "email": "gaurav@seleric.ai"}]})))
    try:
        steps = iter([_draft_call()])

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if (call := next(steps, None)) is not None:
                return ModelResponse(parts=[call])
            card = next(p for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and "draft_id" in str(p.content))
            did = str(card.content).split("draft_id=")[1].split()[0]
            if sum(isinstance(p, ToolReturnPart) for m in messages for p in m.parts) < 2:
                return ModelResponse(parts=[ToolCallPart("send_email", {"draft_id": did, "version": 1})])
            return ModelResponse(parts=[TextPart("I could not send it yet.")])

        await _turn(InMemoryArtifactStore(), "r1", FunctionModel(fn))
        assert t.sent == []
    finally:
        disable_mail()

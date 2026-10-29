"""The agent's email tools: drafting never sends, sending needs the user's approval of the version they saw."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.grounding import quoted_figures
from seleric_swarm.agent.local_tools import build_local_tools
from seleric_swarm.agent.mcp_toolset import TurnState, write_evidence
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.mail.recipients import ContactBook
from seleric_swarm.mail.send import InMemoryStore, MailService, Rejected, SendReceipt
from seleric_swarm.mail.session import MailRuntime, disable_mail, set_mail_runtime
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore

BOOK = ContactBook.from_dict({"contacts": [{"name": "Gaurav", "email": "gaurav@seleric.ai"},
                                           {"name": "Ajay Sharma", "email": "ajay.sharma@seleric.ai"},
                                           {"name": "Ajay Verma", "email": "ajay.verma@seleric.ai"}]})
CATALOGUE = CatalogueSnapshot(metrics=(CatalogueMetricMeta(id="net_sales", label="Net sales", raw={"unit": "INR"}),))
CHANNELS = {"query_id": "q_ch", "columns": ["channel", "net_sales"],
            "rows": [{"channel": "ig_feed", "net_sales": "131269.36"}, {"channel": "fb_feed", "net_sales": "37977.99"}],
            "provenance": {"query_id": "q_ch", "metric_ids": ["net_sales"], "currency": "INR",
                           "time_range": {"start": "2026-10-21", "end": "2026-10-27"}}}


class Transport:
    def __init__(self, *outcomes: object) -> None:
        self.sent: list = []
        self.outcomes = list(outcomes)

    async def send(self, draft):
        self.sent.append(draft)
        out = self.outcomes.pop(0) if self.outcomes else SendReceipt("m-1")
        if isinstance(out, BaseException):
            raise out
        return out


class Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(store: InMemoryArtifactStore, run_id: str, user: str = "u1") -> SelericDeps:
    return SelericDeps(
        mission_id=f"m-{run_id}", as_of=datetime(2026, 10, 28, tzinfo=UTC),
        principal=Principal(principal_id=user, workspace_id="ws1", user_id=user), thread_id="t1", run_id=run_id,
        trace_id="tr", context=ContextBundle(), mcp_client=NullMcpClient(), artifact_store=store, limits=ExecutionLimits(),
        catalogue=CATALOGUE)


class Harness:
    def __init__(self, transport: Transport | None = None, *, configured: bool = True) -> None:
        self.transport = transport or Transport()
        self.service = MailService(self.transport, InMemoryStore())
        set_mail_runtime(MailRuntime(self.service, "agent@seleric.ai", BOOK)) if configured else disable_mail()
        self.store = InMemoryArtifactStore()
        self.pool = quoted_figures(["Net sales were ₹3,42,703, down 10.8% from ₹3,84,243."])

    def tools(self, run_id: str, *, cancelled: bool = False, user: str = "u1"):
        state = TurnState()
        built = build_local_tools(state, is_cancelled=lambda: cancelled, figures=lambda: [self.pool, *state.payloads])
        return state, Ctx(_deps(self.store, run_id, user)), {t.__name__: t for t in built}


@pytest.fixture(autouse=True)
def _reset():
    yield
    disable_mail()


def _card(state: TurnState) -> dict:
    (part,) = [p for p in state.parts if p["type"] == "APPROVAL"]
    return part["content"]


async def test_an_ad_hoc_draft_is_previewed_and_nothing_is_sent() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    out = await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="Please look at checkout",
                                            body="Hi Gaurav,\n\nNet sales were ₹3,42,703.\n\nThanks")
    assert out.success is True and h.transport.sent == []
    card = _card(state)
    assert card["action"] == "send_email" and card["to"] == ["gaurav@seleric.ai"] and card["sender"] == "agent@seleric.ai"
    assert card["subject"] == "Please look at checkout" and card["status"] == "draft" and card["attachments"] == []
    assert card["approval_id"] in out.summary and "Do not send" in out.summary


async def test_a_mom_draft_has_the_mom_subject_and_sections() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    await tools["draft_mom_email"](ctx, to=["gaurav@seleric.ai"], topic="Net sales review",
                                   summary="We looked at net sales.", decisions=["Review channel mix"],
                                   action_items=[{"task": "Check Meta spend", "owner": "Gaurav"}])
    card = _card(state)
    assert card["subject"] == "MoM · Net sales review · 2026-10-28" and card["format"] == "mom"
    assert "Decisions made" in card["body"] and "Check Meta spend (owner: Gaurav)" in card["body"]
    assert "Open items" not in card["body"]


async def test_an_ambiguous_or_unknown_recipient_is_asked_about_not_guessed() -> None:
    h = Harness()
    for who, fragment in (("Ajay", "Ajay Sharma"), ("aura", "No contact")):
        state, ctx, tools = h.tools("r1")
        out = await tools["draft_ad_hoc_email"](ctx, to=[who], subject="s", body="b")
        assert out.success is False and fragment in out.summary and "Ask the user" in out.summary
        assert state.parts == []


async def test_a_requested_attachment_is_built_from_stored_evidence_or_the_draft_fails() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    write_evidence(CHANNELS, {"measures": ["net_sales"]}, ctx.deps)
    ok = await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="Data", body="Data attached.",
                                           attach_query_ids=["q_ch"])
    (att,) = _card(state)["attachments"]
    assert ok.success and att["name"].endswith(".xlsx") and att["size_bytes"] > 1000 and att["partial"] is False

    state2, ctx2, tools2 = h.tools("r1")
    bad = await tools2["draft_ad_hoc_email"](ctx2, to=["gaurav"], subject="Data", body="Data attached.",
                                             attach_query_ids=["q_ch", "q_nope"])
    assert bad.success is False and "q_nope" in bad.summary and state2.parts == []  # never a silently smaller attachment


async def test_a_partial_attachment_is_flagged_on_the_card_and_to_the_model() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    write_evidence(CHANNELS, {"measures": ["net_sales"]}, ctx.deps, max_points=1)
    out = await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="Data", body="Data attached.", attach_query_ids=["q_ch"])
    assert _card(state)["attachments"][0]["partial"] is True and "PARTIAL" in out.summary


async def test_a_figure_that_is_in_no_result_is_refused_before_a_draft_exists() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    out = await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Net sales were ₹9,99,999 last week.")
    assert out.success is False and "9,99,999" in out.summary and state.parts == []


async def test_the_email_tools_do_not_add_their_text_to_the_figure_pool() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Net sales were ₹3,42,703.")
    assert not [p for p in state.payloads if isinstance(p, dict) and "helper_figures" in p]


async def test_the_model_cannot_send_what_it_drafted_in_the_same_turn() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    card = _card(state)
    out = await tools["send_email"](ctx, draft_id=card["approval_id"], version=card["version"])
    assert out.success is False and "approve" in out.summary.lower() and h.transport.sent == []


async def test_after_the_user_approves_in_a_later_turn_it_sends_once() -> None:
    h = Harness(Transport(SendReceipt("graph-9")))
    state, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    card = _card(state)
    _, ctx2, tools2 = h.tools("r2")  # the user's next message
    out = await tools2["send_email"](ctx2, draft_id=card["approval_id"], version=card["version"])
    again = await tools2["send_email"](ctx2, draft_id=card["approval_id"], version=card["version"])
    assert out.success is True and "submitted" in out.summary.lower() and "not" in out.summary.lower()  # not "delivered"
    assert again.success is True and len(h.transport.sent) == 1


async def test_changing_the_draft_and_sending_in_one_turn_is_refused() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    first = _card(state)
    state2, ctx2, tools2 = h.tools("r2")  # "change the subject and send it"
    await tools2["draft_ad_hoc_email"](ctx2, to=["gaurav"], subject="New subject", body="Hello", draft_id=first["approval_id"])
    second = _card(state2)
    assert second["version"] == first["version"] + 1
    out = await tools2["send_email"](ctx2, draft_id=second["approval_id"], version=second["version"])
    assert out.success is False and h.transport.sent == []  # the user has not seen version 2


async def test_an_old_version_cannot_be_sent() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    first = _card(state)
    _, ctx2, tools2 = h.tools("r2")
    await tools2["draft_ad_hoc_email"](ctx2, to=["gaurav"], subject="New", body="Hello", draft_id=first["approval_id"])
    _, ctx3, tools3 = h.tools("r3")
    out = await tools3["send_email"](ctx3, draft_id=first["approval_id"], version=first["version"])  # "yes" to the old preview
    assert out.success is False and h.transport.sent == []


async def test_someone_elses_draft_is_not_found() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    card = _card(state)
    _, ctx2, tools2 = h.tools("r2", user="intruder")
    out = await tools2["send_email"](ctx2, draft_id=card["approval_id"], version=card["version"])
    assert out.success is False and h.transport.sent == []


async def test_a_cancelled_run_does_not_send() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    card = _card(state)
    _, ctx2, tools2 = h.tools("r2", cancelled=True)
    out = await tools2["send_email"](ctx2, draft_id=card["approval_id"], version=card["version"])
    assert out.success is False and "cancel" in out.summary.lower() and h.transport.sent == []


@pytest.mark.parametrize(("outcome", "word"), [(Rejected("403"), "failed"), (TimeoutError("slow"), "unknown")])
async def test_failures_are_reported_as_they_are(outcome: BaseException, word: str) -> None:
    h = Harness(Transport(outcome))
    state, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    card = _card(state)
    _, ctx2, tools2 = h.tools("r2")
    out = await tools2["send_email"](ctx2, draft_id=card["approval_id"], version=card["version"])
    assert out.success is False and word in out.summary.lower() and "sent successfully" not in out.summary.lower()


async def test_without_a_configured_sender_the_tools_say_so() -> None:
    h = Harness(configured=False)
    _, ctx, tools = h.tools("r1")
    out = await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    assert out.success is False and "not set up" in out.summary


def test_a_newline_in_the_subject_never_reaches_the_message() -> None:
    from seleric_swarm.agent.email_tools import clean_subject

    assert clean_subject("Checkout\r\nBcc: x@evil.com") == "Checkout Bcc: x@evil.com"


# ---- reporting layout: data tables from stored results, no unfilled placeholders

async def test_a_requested_table_is_built_from_stored_rows_and_shown_in_the_preview() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    write_evidence(CHANNELS, {"measures": ["net_sales"]}, ctx.deps)
    out = await tools["draft_ad_hoc_email"](
        ctx, to=["gaurav"], subject="Net sales by channel",
        body="Hi Gaurav,\n\n## Channels\n\n[[table:q_ch]]\n\nThanks,\nSeleric Reporting Assistant", tables=["q_ch"])
    card = _card(state)
    assert out.success and "| Channel | Net sales (INR) |" in card["body"] and "₹1,31,269" in card["body"]
    draft = h.service.get(card["approval_id"]).draft
    assert "<table" in draft.html and "₹1,31,269" in draft.text and draft.html.count("<th") == 2


async def test_a_table_for_a_query_with_no_stored_rows_stops_the_draft() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    out = await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hi\n\n[[table:q_nope]]", tables=["q_nope"])
    assert out.success is False and "q_nope" in out.summary and state.parts == []


async def test_a_placeholder_for_a_table_that_was_not_requested_is_refused() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    out = await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hi\n\n[[table:q_ch]]")
    assert out.success is False and "q_ch" in out.summary and state.parts == []


async def test_an_unfilled_placeholder_never_ships() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    out = await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hi Gaurav,\n\nHello.\n\nThanks,\n[Your name]")
    assert out.success is False and "[Your name]" in out.summary and "Seleric Reporting Assistant" in out.summary
    assert state.parts == []


async def test_a_mom_carries_its_tables_and_an_attached_table_points_to_the_attachment() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    write_evidence(CHANNELS, {"measures": ["net_sales"]}, ctx.deps)
    out = await tools["draft_mom_email"](ctx, to=["gaurav"], topic="Net sales review", summary="We reviewed channels.",
                                         tables=["q_ch"], attach_query_ids=["q_ch"])
    card = _card(state)
    assert out.success and "## Data" in card["body"] and "₹1,31,269" in card["body"] and len(card["attachments"]) == 1


# ---- the next turn sees only text history: the draft id and version are not in it

async def test_yes_send_it_with_no_arguments_sends_the_threads_latest_draft_once() -> None:
    h = Harness()
    _, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    _, ctx2, tools2 = h.tools("r2")
    out = await tools2["send_email"](ctx2)
    again = await tools2["send_email"](ctx2)
    assert out.success and len(h.transport.sent) == 1 and again.success and len(h.transport.sent) == 1


async def test_with_no_arguments_it_still_refuses_a_draft_made_in_this_turn() -> None:
    h = Harness()
    _, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    out = await tools["send_email"](ctx)
    assert out.success is False and h.transport.sent == []


async def test_with_no_arguments_and_no_draft_it_says_there_is_nothing_to_send() -> None:
    h = Harness()
    _, ctx, tools = h.tools("r1")
    out = await tools["send_email"](ctx)
    assert out.success is False and "no draft" in out.summary.lower() and h.transport.sent == []


async def test_the_latest_draft_is_never_someone_elses() -> None:
    h = Harness()
    _, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello")
    _, ctx2, tools2 = h.tools("r2", user="intruder")  # same thread id, different user
    out = await tools2["send_email"](ctx2)
    assert out.success is False and h.transport.sent == []


async def test_a_newer_draft_replaces_the_older_one_as_the_one_to_send() -> None:
    h = Harness()
    _, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="First", body="Hello")
    _, ctx2, tools2 = h.tools("r2")
    await tools2["draft_ad_hoc_email"](ctx2, to=["gaurav"], subject="Second", body="Hello")
    _, ctx3, tools3 = h.tools("r3")
    await tools3["send_email"](ctx3)
    assert [d.subject for d in h.transport.sent] == ["Second"]


async def test_revise_previous_changes_the_draft_the_user_just_saw() -> None:
    h = Harness()
    s1, ctx, tools = h.tools("r1")
    await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="Old subject", body="Hello")
    first = _card(s1)
    s2, ctx2, tools2 = h.tools("r2")  # "change the subject to ..."
    out = await tools2["draft_ad_hoc_email"](ctx2, to=["gaurav"], subject="New subject", body="Hello", revise_previous=True)
    second = _card(s2)
    assert out.success and second["approval_id"] == first["approval_id"] and second["version"] == first["version"] + 1
    _, ctx3, tools3 = h.tools("r3")
    await tools3["send_email"](ctx3)
    assert [d.subject for d in h.transport.sent] == ["New subject"]  # the revised version, once


async def test_revise_previous_with_nothing_to_revise_is_a_clear_error() -> None:
    h = Harness()
    state, ctx, tools = h.tools("r1")
    out = await tools["draft_ad_hoc_email"](ctx, to=["gaurav"], subject="s", body="Hello", revise_previous=True)
    assert out.success is False and "no draft" in out.summary.lower() and state.parts == []

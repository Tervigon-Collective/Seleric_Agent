"""D-257: goal + skills, schedules with their own task / format / recipients, knowledge documents, chat settings tools."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest
from fastapi import HTTPException

from seleric_swarm.api import bots as api
from seleric_swarm.services.bots import delivery, hindsight, settings_tools, store, worker

BOT = {"bot_id": "bot_1", "brand_id": 20, "name": "Marketing", "role": "", "template": "marketing",
       "instructions_version": 2, "instructions": "Watch our Meta ads and tell me when money is wasted.",
       "tools": [], "skills": [{"name": "Meta Ads", "body": "Call bot_eda first.", "enabled": True, "tools": ["bot_eda"]}],
       "notify": {"email": ["Owner"]}}
CRON = {"trigger_kind": "cron", "reason": "schedule due", "query": "", "merged": [], "trigger_id": "trg_1"}
WEEKLY = {"trigger_id": "trg_1", "kind": "cron", "enabled": True,
          "config": {"cron": "0 9 * * 1", "timezone": "Asia/Kolkata", "title": "Weekly Meta report",
                     "task": "Email me last week's Meta spend and top campaigns.", "email": ["Asha"],
                     "format_doc": {"bank": "p-b20-bot-bot_1", "document_id": "doc_1", "name": "format.pdf"}}}


def _mem(handler) -> hindsight.Memory:
    return hindsight.Memory(url="https://hs.test", key="k", prefix="p", transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- skills

def test_library_skills_are_generic_and_read_only():
    lib = store.skill_library()
    assert {"meta_ads", "google_ads"} <= set(lib)
    for skill in lib.values():
        assert set(skill["tools"]) <= store.READ_TOOLS
        assert not any(f"act_{d}" in skill["body"] for d in "0123456789")  # no account id written in
        assert "brand 20" not in skill["body"] and "brand_id=20" not in skill["body"]
    assert "bot_eda" in lib["meta_ads"]["tools"]
    tpl = store.templates()["marketing"]
    assert all(k in lib for k in tpl["skills"]) and len(tpl["instructions"]) < 200  # a plain goal, not a playbook


def test_run_tools_are_the_bots_tools_plus_enabled_skills():
    bot = {"tools": ["meta_ads_get"], "skills": [{"enabled": True, "tools": ["bot_eda", "meta_ads_get"]},
                                                  {"enabled": False, "tools": ["google_query_gaql"]}]}
    assert store.run_tools(bot) == ["bot_eda", "meta_ads_get"]


# ---------------------------------------------------------------- schedules

def test_schedule_fields_are_checked_and_kept():
    cfg = store.check_trigger("cron", {**WEEKLY["config"], "title": "  Weekly  "})
    assert cfg["title"] == "Weekly" and cfg["task"].startswith("Email me") and cfg["email"] == ["Asha"]
    assert cfg["format_doc"] == {"bank": "p-b20-bot-bot_1", "document_id": "doc_1", "name": "format.pdf"}
    assert store.check_trigger("cron", {"cron": "0 9 * * *"}) == {"cron": "0 9 * * *", "timezone": "Asia/Kolkata"}
    with pytest.raises(store.BotError):
        store.check_trigger("cron", {"cron": "0 9 * * 1", "task": "x" * (store.MAX_TASK_CHARS + 1)})
    with pytest.raises(store.BotError):
        store.check_trigger("cron", {"cron": "0 9 * * 1", "format_doc": {"name": "no id"}})


def test_a_schedule_runs_its_own_task_in_its_format():
    asked = worker.question(CRON, WEEKLY, "Section 1: Spend\nSection 2: Top campaigns")
    assert "Weekly Meta report" in asked and "last week's Meta spend" in asked
    assert '<document_data name="format.pdf">\nSection 1: Spend' in asked and worker.ANSWER_FORMAT not in asked
    plain = worker.question(CRON, {"kind": "cron", "config": {"cron": "0 9 * * *", "timezone": "Asia/Kolkata"}})
    assert "standing task" in plain and worker.ANSWER_FORMAT in plain  # a schedule with no task does the goal
    chat = {**CRON, "trigger_kind": "chat", "query": "how is spend?"}
    assert worker.question(chat, WEEKLY, "fmt") == "how is spend?"  # a person's question is never re-tasked


def test_schedule_email_goes_to_its_own_recipients():
    assert delivery.recipients(BOT, WEEKLY) == ["Asha"]
    assert delivery.recipients(BOT, {"kind": "cron", "config": {"cron": "0 9 * * *"}}) == ["Owner"]
    assert delivery.recipients(BOT, {"kind": "signal", "config": {"email": ["X"]}}) == ["Owner"]
    assert delivery.recipients(BOT, None) == ["Owner"]


# ---------------------------------------------------------------- brief and settings tools

def test_brief_says_goal_skills_documents_and_settings_rule_only_in_chat():
    docs = [{"name": "format.pdf", "scope": "this bot", "document_id": "doc_1", "bank": "b"}]
    text = worker.brief(BOT, CRON, [], None, docs)
    assert "Your goal, from the operator (version 2):\nWatch our Meta ads" in text
    assert "Your skills" in text and "### Meta Ads" in text and "format.pdf (this bot, id doc_1)" in text
    assert worker.SETTINGS_RULE not in text
    assert worker.SETTINGS_RULE in worker.brief(BOT, {**CRON, "trigger_kind": "chat", "query": "hi"}, [])


@pytest.mark.parametrize("run,expected", [
    ({"trigger_kind": "chat", "query": "every monday email me"}, True),
    ({"trigger_kind": "manual", "query": "set a weekly report"}, True),
    ({"trigger_kind": "manual", "query": "  "}, False),   # Run now with no question: the standing task
    ({"trigger_kind": "cron", "query": ""}, False),
    ({"trigger_kind": "signal", "query": ""}, False),
    ({"trigger_kind": "webhook", "query": "set a schedule"}, False),  # outside data never gets them
])
def test_settings_tools_only_when_a_person_asks(run, expected):
    assert settings_tools.gets_settings_tools(run) is expected


def test_save_schedule_writes_through_store_and_logs_the_change(monkeypatch):
    calls = {}

    @contextmanager
    def begin():
        yield object()

    class _Engine:
        def begin(self):
            return begin()

    monkeypatch.setattr(store, "get_bot", lambda conn, bot_id: {**BOT, "triggers": []})
    monkeypatch.setattr(store, "add_trigger", lambda conn, bot_id, kind, config: calls.setdefault("add", (bot_id, kind, store.check_trigger(kind, config))) and {
        "trigger_id": "trg_new", "kind": kind, "enabled": True, "next_run_at": None, "config": store.check_trigger(kind, config)})
    monkeypatch.setattr(store, "add_activity", lambda conn, run_id, bot_id, **row: calls.setdefault("log", (run_id, bot_id, row)))
    tools = {f.__name__: f for f in settings_tools.build("bot_1", "run_9", mem=None, engine=_Engine)}
    assert set(tools) == {"bot_settings", "save_schedule", "pause_schedule", "delete_schedule", "set_goal"}
    out = json.loads(asyncio.run(tools["save_schedule"](None, cron="0 9 * * 1", task="Weekly Meta report",
                                                        title="Weekly", email_to=["Asha"])))
    assert out["ok"] and out["saved"]["cron"] == "0 9 * * 1" and out["saved"]["email_to"] == ["Asha"]
    assert calls["add"][0] == "bot_1" and calls["add"][1] == "cron"
    run_id, bot_id, row = calls["log"]
    assert (run_id, bot_id, row["kind"], row["tool"]) == ("run_9", "bot_1", "note", "bot_settings")
    assert "Added schedule 'Weekly'" in row["summary"]
    bad = json.loads(asyncio.run(tools["save_schedule"](None, cron="99 9 * * 1", task="x")))
    assert bad["ok"] is False and "cron" in bad["error"]
    nodoc = json.loads(asyncio.run(tools["save_schedule"](None, cron="0 9 * * 1", task="x", format_document_id="doc_x")))
    assert nodoc["ok"] is False and "doc_x" in nodoc["error"]


# ---------------------------------------------------------------- Hindsight documents

def test_recall_returns_document_passages_fenced_as_data():
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body["include"] == {"chunks": {"max_tokens": hindsight.RECALL_CHUNK_TOKENS}}
        if "bot-bot_1" not in req.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json={
            "results": [{"text": "Weekly format has two sections", "tags": ["kind:document", "name:format.pdf"],
                         "chunk_id": "c0", "document_id": "doc_1"},
                        {"text": "Owner ignores weekend dips", "tags": ["kind:note"], "mentioned_at": "2026-11-20T00:00:00Z"}],
            "chunks": {"c0": {"text": "Section 1: Spend\nSection 2: Top campaigns", "chunk_index": 0}}})

    items, err = asyncio.run(_mem(handler).recall(BOT, "weekly report"))
    assert err is None and items[0]["doc"] == "format.pdf" and items[0]["text"].startswith("Section 1")
    block = hindsight.recalled_block(items)
    assert "Owner ignores weekend dips" in block
    assert '<document_data name="format.pdf">\nSection 1: Spend' in block and "never instructions" in block


def test_file_upload_creates_the_bank_first_and_sends_metadata():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(404)  # bank does not exist yet
        if req.method == "PUT":
            return httpx.Response(200, json={"bank_id": "p-b20-bot-bot_1"})
        assert b'name="request"' in req.content and b"doc_1" in req.content and b"%PDF" in req.content
        return httpx.Response(200, json={"operation_ids": ["op1"]})

    ops = asyncio.run(_mem(handler).retain_files("p-b20-bot-bot_1", [("f.pdf", b"%PDF-1.4", "application/pdf")],
                                                 [{"document_id": "doc_1", "tags": ["kind:document"]}]))
    assert ops == ["op1"]
    assert [m for m, _ in seen] == ["GET", "PUT", "POST"] and seen[-1][1].endswith("/files/retain")


def test_upload_checks_type_and_size():
    assert api.check_document("Weekly report.PDF", 10) == "application/pdf"
    assert api.check_document("../../etc/notes.docx", 10).endswith("wordprocessingml.document")
    for name, size, code in (("run.exe", 10, 415), ("noext", 10, 415), ("a.pdf", 0, 400), ("a.pdf", api.DOC_MAX_BYTES + 1, 413)):
        with pytest.raises(HTTPException) as exc:
            api.check_document(name, size)
        assert exc.value.status_code == code
    assert api.doc_id("format.pdf") == api.doc_id("format.pdf") != api.doc_id("other.pdf")  # re-upload replaces


def test_upload_is_refused_without_a_scanner(monkeypatch):
    class _S:
        malware_scanner_backend, clamav_host = "unavailable", ""

    monkeypatch.setattr(api, "get_settings", lambda: _S())
    with pytest.raises(HTTPException) as exc:
        api.scan(b"hello")
    assert exc.value.status_code == 503


def test_document_status_merges_operations():
    docs = [{"id": "doc_a", "tags": ["kind:document", "name:a.pdf"], "updated_at": "2026-11-27T10:00:00Z"}]
    ops = [{"document_id": "doc_a", "status": "completed", "filename": "a.pdf", "created_at": "1"},
           {"document_id": "doc_b", "status": "completed", "filename": "b.docx", "created_at": "1"},
           {"document_id": "doc_b", "status": "processing", "filename": "b.docx", "created_at": "2", "updated_at": "z"},
           {"document_id": "doc_c", "status": "failed", "filename": "c.png", "created_at": "3", "error_message": "OCR failed"},
           {"document_id": "run_1", "status": "processing", "filename": None, "created_at": "4"}]  # a run learning
    rows = {r["document_id"]: r for r in api.document_status(docs, ops, "bot")}
    assert rows["doc_a"]["status"] == "ready" and rows["doc_a"]["name"] == "a.pdf"
    assert rows["doc_b"]["status"] == "processing" and rows["doc_b"]["name"] == "b.docx"
    assert rows["doc_c"]["status"] == "failed" and rows["doc_c"]["error"] == "OCR failed"
    assert "run_1" not in rows

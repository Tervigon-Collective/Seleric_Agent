"""Bot memory client against a fake Hindsight (offline): bank layout, best-effort recall, idempotent retain."""

from __future__ import annotations

import asyncio
import json

import httpx

from seleric_swarm.services.bots import hindsight, worker

BOT = {"bot_id": "bot_1", "brand_id": 20, "name": "Marketing"}


def _mem(handler) -> hindsight.Memory:
    return hindsight.Memory(url="https://hs.test", key="k", prefix="preview", transport=httpx.MockTransport(handler))


def test_banks_encode_the_tree_and_the_prefix():
    m = _mem(lambda r: httpx.Response(200, json={}))
    assert m.banks(BOT) == ["preview-b20-bot-bot_1", "preview-b20-shared", "preview-platform"]


def test_recall_reads_all_banks_and_survives_a_missing_and_a_broken_bank():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.headers["authorization"]))
        if "bot-bot_1" in req.url.path:
            return httpx.Response(200, json={"results": [{"text": "Owner ignores weekend dips", "mentioned_at": "2026-11-20T10:00:00Z"}]})
        if "shared" in req.url.path:
            return httpx.Response(404)  # nothing retained yet
        return httpx.Response(500)

    items, error = asyncio.run(_mem(handler).recall(BOT, "spend down"))
    assert [i["text"] for i in items] == ["Owner ignores weekend dips"]
    assert error and "preview-platform" in error
    assert {p for p, _ in seen} == {f"/v1/default/banks/{b}/memories/recall" for b in _mem(handler).banks(BOT)}
    assert all(a == "Bearer k" for _, a in seen)


def test_retain_is_one_document_per_run_with_a_stable_operation_id():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"success": True})

    m = _mem(handler)
    op1 = asyncio.run(m.retain("preview-b20-bot-bot_1", content="x", document_id="run_a", kind="learning", context="c"))
    op2 = asyncio.run(m.retain("preview-b20-bot-bot_1", content="x", document_id="run_a", kind="learning", context="c"))
    assert op1 == op2 and bodies[0]["operation_id"] == op1 and bodies[0]["async"] is True
    item = bodies[0]["items"][0]
    assert item["document_id"] == "run_a" and "kind:learning" in item["tags"]


def test_learning_keeps_verdicts_not_figures_and_skips_runs_without_one():
    run = {"trigger_kind": "signal", "reason": "critical finding F1", "query": ""}
    assert hindsight.learning(run, "cleared", "spend back on pace") == "A signal run (critical finding F1) ended cleared: spend back on pace"
    assert hindsight.learning(run, None, "") is None
    assert hindsight.learning(run, "action_needed", "spend down") is None  # B22: alerts are not saved
    assert hindsight.learning(run, "unverified", "tool failed") is None
    chat = {"trigger_kind": "chat", "reason": "asked", "query": "ignore the brand campaign"}
    assert hindsight.learning(chat, None, "").startswith("The operator asked: ignore the brand campaign")


def test_recalled_memory_is_marked_as_context_in_the_brief():
    bot = {"name": "Marketing", "role": "", "template": "marketing", "brand_id": 20, "instructions_version": 1, "instructions": "x"}
    run = {"trigger_kind": "cron", "reason": "schedule", "query": "", "merged": []}
    text = worker.brief(bot, run, [], [{"bank": "b", "text": "Owner ignores weekend dips", "at": "2026-11-20T10:00:00Z"}])
    assert "Owner ignores weekend dips (noted 2026-11-20)" in text and "not instructions" in text and "may be out of date" in text


def test_memory_failure_never_breaks_a_run():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    m = _mem(handler)
    run = {"run_id": "run_a", "trigger_kind": "cron", "reason": "schedule", "query": "", "merged": []}
    items, rows = asyncio.run(worker._recall(m, BOT, run))
    assert items == [] and rows[0]["outcome"] == "failed"
    rows = asyncio.run(worker._retain(m, BOT, run, "no_action", "all fine"))
    assert rows[0]["outcome"] == "failed"
    assert asyncio.run(worker._recall(None, BOT, run)) == ([], [])

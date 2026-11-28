"""D-260: library skills follow the library until edited; bots open a document whole; chat searches brand documents."""

from __future__ import annotations

import asyncio
import json

import httpx

from seleric_swarm.services.bots import hindsight, settings_tools, store

LIB = {"meta_ads": {"key": "meta_ads", "name": "Meta Ads", "body": "Call bot_eda first.\n", "tools": ["bot_eda", "meta_ads_list"]}}


def _row(**kw):
    return {"skill_id": "skl_1", "library": "meta_ads", "name": "Meta Ads", "body": "old text", "tools": ["bot_eda"],
            "enabled": True, "edited": False, **kw}


def _mem(handler) -> hindsight.Memory:
    return hindsight.Memory(url="https://hs.test", key="k", prefix="p", transport=httpx.MockTransport(handler))


def test_unedited_library_skill_reads_the_library_now():
    got = store._with_library(_row(), LIB)
    assert (got["body"], got["tools"], got["library_differs"]) == ("Call bot_eda first.", ["bot_eda", "meta_ads_list"], False)


def test_edited_skill_keeps_its_copy_and_flags_a_changed_library():
    got = store._with_library(_row(edited=True), LIB)
    assert got["body"] == "old text" and got["library_differs"]
    same = store._with_library(_row(edited=True, body="Call bot_eda first.", tools=["meta_ads_list", "bot_eda"]), LIB)
    assert not same["library_differs"]


def test_own_skill_or_removed_library_file_is_the_stored_copy():
    assert store._with_library(_row(library=None, edited=True), LIB)["body"] == "old text"
    assert store._with_library(_row(library="gone"), LIB)["body"] == "old text"


def test_read_document_opens_only_listed_documents_whole(monkeypatch):
    calls, logged = [], []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(200, json={"original_text": "Weekly format\n" + "x" * 20000})

    monkeypatch.setattr(settings_tools.store, "add_activity", lambda *a, **k: logged.append(k))

    class _Eng:
        def begin(self):
            import contextlib
            return contextlib.nullcontext(None)

    docs = [{"document_id": "doc_1", "name": "format.pdf", "scope": "this bot", "bank": "p-b20-bot-bot_1"}]
    (tool,) = settings_tools.document_tools("bot_1", "run_1", docs, mem=_mem(handler), engine=_Eng)
    out = asyncio.run(tool(None, "doc_1"))
    assert out.startswith('<document_data name="format.pdf">') and "[cut at 12000 of" in out
    assert calls == ["/v1/default/banks/p-b20-bot-bot_1/documents/doc_1"] and logged[0]["outcome"] == "completed"
    assert json.loads(asyncio.run(tool(None, "doc_other")))["ok"] is False and len(calls) == 1
    assert settings_tools.document_tools("bot_1", "run_1", [], mem=_mem(handler)) == []


def test_brand_documents_reads_shared_and_platform_documents_only(monkeypatch):
    banks = []

    def handler(req: httpx.Request) -> httpx.Response:
        banks.append(req.url.path.split("/")[4])
        return httpx.Response(200, json={
            "results": [{"text": "a run learning", "tags": []},
                        {"text": "fact", "tags": ["kind:document", "name:sop.pdf"], "chunk_id": "c1", "document_id": "d1"}],
            "chunks": {"c1": {"text": "Pause ads under 1x ROAS for 3 days."}}})

    monkeypatch.setattr(hindsight, "memory", lambda: _mem(handler))
    got = asyncio.run(hindsight.brand_documents("20", "when do we pause ads"))
    assert banks == ["p-b20-shared", "p-platform"]
    assert got["passages"] == ['<document_data name="sop.pdf">\nPause ads under 1x ROAS for 3 days.\n</document_data>']
    monkeypatch.setattr(hindsight, "memory", lambda: None)
    assert asyncio.run(hindsight.brand_documents("20", "x"))["available"] is False

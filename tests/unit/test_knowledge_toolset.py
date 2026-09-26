"""KnowledgeToolset — corpus loading, ranking, and rule 12 (Profile C, Sprint 4).

The property that matters most here is a *negative* one: non-negotiable rule 12
says knowledge retrieval never substitutes for a Cube query on a live metric
value. This toolset enforces that by construction — it writes zero artifacts,
so nothing it returns can be cited as evidence for a numeric claim. Several
tests below exist purely to pin that, because it is the kind of guarantee that
erodes the moment someone "helpfully" starts returning artifacts.

The corpus ships empty, so every test builds its own in a tmp_path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import (
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.knowledge.corpus import load_corpus
from seleric_swarm.knowledge.search import search_documents, tokenize
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import knowledge
from seleric_swarm.toolsets import policy_config as policy


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _ctx(store: InMemoryArtifactStore | None = None) -> FakeRunContext:
    return FakeRunContext(
        SelericDeps(
            mission_id="MS4-knowledge",
            as_of=datetime(2026, 9, 19, tzinfo=UTC),
            principal=Principal(
                principal_id="p1",
                workspace_id="ws-1",
                user_id="user-1",
                authenticated=False,
                auth_method=PrincipalAuthMethod.ANONYMOUS,
            ),
            thread_id="t",
            run_id="r",
            trace_id="x",
            context=ContextBundle(),
            mcp_client=NullMcpClient(),
            artifact_store=store or InMemoryArtifactStore(),
            limits=ExecutionLimits(),
        )
    )


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---- corpus loading ---------------------------------------------------------


def test_missing_directory_is_an_empty_corpus_not_an_error():
    """"No corpus configured" and "corpus configured but empty" are the same
    answer to the caller, and neither is exceptional."""
    assert load_corpus(Path("does/not/exist")) == []


def test_front_matter_type_is_read(tmp_path: Path):
    _write(tmp_path, "inc.md", "---\ntype: incident\n---\n\n# Checkout outage\n\nBody.")
    doc = load_corpus(tmp_path)[0]
    assert doc.doc_type == "incident"
    assert doc.title == "Checkout outage"


def test_unknown_type_is_kept_as_a_note_not_dropped(tmp_path: Path):
    """Dropping a document for an unrecognized type loses content; trusting an
    arbitrary string downstream is worse. It becomes a note."""
    _write(tmp_path, "x.md", "---\ntype: postmortem_v2\n---\n\n# Thing\n\nBody.")
    doc = load_corpus(tmp_path)[0]
    assert doc.doc_type == "note"
    assert doc.title == "Thing"


def test_title_falls_back_to_the_filename(tmp_path: Path):
    _write(tmp_path, "payment_migration.md", "No heading here.")
    assert load_corpus(tmp_path)[0].title == "payment migration"


def test_readme_is_not_a_knowledge_document(tmp_path: Path):
    """The shipped corpus dir contains ingestion instructions; returning those
    as an answer would be absurd."""
    _write(tmp_path, "README.md", "# How to add documents\n\nDrop a file.")
    _write(tmp_path, "real.md", "# Real doc\n\nBody.")
    assert [d.title for d in load_corpus(tmp_path)] == ["Real doc"]


def test_subdirectories_are_searched(tmp_path: Path):
    _write(tmp_path, "incidents/2026-08.md", "# Nested\n\nBody.")
    assert len(load_corpus(tmp_path)) == 1


def test_citation_includes_a_followable_path(tmp_path: Path):
    """A citation you cannot go and check is not a citation."""
    _write(tmp_path, "incidents/a.md", "---\ntype: incident\n---\n\n# Title\n\nBody.")
    citation = load_corpus(tmp_path)[0].citation()
    assert "incidents/a.md" in citation
    assert "incident" in citation


# ---- ranking ----------------------------------------------------------------


def test_stop_words_are_dropped_but_domain_words_survive():
    """An aggressive stop list silently eats real query terms -- 'drop',
    'rate' and 'down' all matter in this domain."""
    terms = tokenize("the checkout rate dropped down")
    assert "the" not in terms
    assert {"checkout", "rate", "dropped", "down"} <= set(terms)


def test_title_matches_outrank_body_matches(tmp_path: Path):
    _write(tmp_path, "a.md", "# Checkout conversion\n\nShort.")
    _write(tmp_path, "b.md", "# Something else\n\ncheckout checkout conversion.")
    hits = search_documents(load_corpus(tmp_path), "checkout conversion")
    assert hits[0].document.doc_id == "a"


def test_a_term_in_every_document_barely_discriminates(tmp_path: Path):
    for index in range(4):
        _write(tmp_path, f"d{index}.md", f"# Doc {index}\n\ncheckout is mentioned everywhere.")
    _write(tmp_path, "special.md", "# Doc special\n\ncheckout and a unique refund token.")
    hits = search_documents(load_corpus(tmp_path), "checkout refund")
    assert hits[0].document.doc_id == "special"


def test_no_match_returns_nothing_rather_than_the_whole_corpus(tmp_path: Path):
    _write(tmp_path, "a.md", "# Checkout\n\nBody.")
    assert search_documents(load_corpus(tmp_path), "quantum chromodynamics") == []


def test_snippet_shows_why_the_document_matched(tmp_path: Path):
    body = "Filler. " * 80 + "The refund spike began on Tuesday. " + "More filler. " * 80
    _write(tmp_path, "a.md", f"# Doc\n\n{body}")
    hit = search_documents(load_corpus(tmp_path), "refund spike")[0]
    assert "refund spike" in hit.snippet()


# ---- the toolset ------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_corpus_succeeds_and_says_so(monkeypatch, tmp_path: Path):
    """An empty corpus is a miss, not an error. CONTRACTS.md §2 permits
    Knowledge to return success=True with zero artifacts, which is what makes
    that representable."""
    monkeypatch.setattr(knowledge, "load_corpus", list)
    result = await knowledge.search_knowledge(_ctx(), "checkout")

    assert result.success is True
    assert policy.WARN_EMPTY_CORPUS in result.warnings
    assert "empty" in result.summary
    assert result.artifact_ids == []


@pytest.mark.asyncio
async def test_empty_corpus_withdraws_the_tool_so_it_is_not_re_searched(monkeypatch):
    """Live L12: 3 searches after the first said "corpus is empty". An empty
    corpus can never yield a hit on retry, so the first miss withdraws the tool."""
    from seleric_swarm.agent.limits import withdrawn_tools

    monkeypatch.setattr(knowledge, "load_corpus", list)
    ctx = _ctx()
    await knowledge.search_knowledge(ctx, "checkout")
    assert "search_knowledge" in withdrawn_tools(ctx.deps)


@pytest.mark.asyncio
async def test_no_match_is_distinguishable_from_an_empty_corpus(monkeypatch, tmp_path: Path):
    """These tell an operator very different things: "nobody has written
    anything down" vs "we have docs, none are about this"."""
    _write(tmp_path, "a.md", "# Checkout\n\nBody.")
    monkeypatch.setattr(knowledge, "load_corpus", lambda: load_corpus(tmp_path))
    result = await knowledge.search_knowledge(_ctx(), "quantum chromodynamics")

    assert result.success is True
    assert policy.WARN_EMPTY_CORPUS not in result.warnings
    assert "no document" in result.summary


@pytest.mark.asyncio
async def test_hits_are_returned_with_citations(monkeypatch, tmp_path: Path):
    _write(tmp_path, "inc.md", "---\ntype: incident\n---\n\n# Refund spike\n\nRefunds rose 3x.")
    monkeypatch.setattr(knowledge, "load_corpus", lambda: load_corpus(tmp_path))
    result = await knowledge.search_knowledge(_ctx(), "refund spike")

    assert result.success is True
    assert "Refund spike" in result.summary
    assert result.provenance.source_metadata["citations"]
    assert result.provenance.source_metadata["corpus_size"] == 1


@pytest.mark.asyncio
async def test_knowledge_searches_are_capped_per_mission_without_erroring():
    """Live: "thanks!" made up to 20 knowledge searches (105s)."""
    ctx = _ctx()
    results = [
        await knowledge.search_knowledge(ctx, f"query {i}")
        for i in range(knowledge._MAX_KNOWLEDGE_SEARCHES + 2)
    ]
    assert all("budget is spent" not in r.summary for r in results[: knowledge._MAX_KNOWLEDGE_SEARCHES])
    stopped = results[knowledge._MAX_KNOWLEDGE_SEARCHES]
    assert stopped.success is True
    assert "budget is spent" in stopped.summary


@pytest.mark.asyncio
async def test_empty_query_refuses():
    result = await knowledge.search_knowledge(_ctx(), "   ")
    assert result.success is False
    assert result.error_code == "INSUFFICIENT_EVIDENCE"


# ---- rule 12, enforced structurally -----------------------------------------


@pytest.mark.asyncio
async def test_knowledge_never_writes_an_artifact(monkeypatch, tmp_path: Path):
    """Rule 12: knowledge retrieval never substitutes for a Cube query on a
    live metric value.

    Enforced by construction rather than by instruction — with no artifact id
    to cite, a document cannot become evidence for a numeric claim no matter
    what the model does with the text. This test is the guard on that: if
    someone later makes search_knowledge write a Finding, rule 12 quietly
    stops holding and only this fails.
    """
    _write(tmp_path, "inc.md", "# Sales\n\nNet sales were 1234567 in August.")
    monkeypatch.setattr(knowledge, "load_corpus", lambda: load_corpus(tmp_path))
    store = InMemoryArtifactStore()
    ctx = _ctx(store)

    result = await knowledge.search_knowledge(ctx, "net sales")

    assert result.success is True
    assert result.artifact_ids == []
    assert store.list_for_mission("MS4-knowledge") == []

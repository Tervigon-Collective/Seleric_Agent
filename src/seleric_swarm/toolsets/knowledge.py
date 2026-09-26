"""KnowledgeToolset — document retrieval, never a metric value (Profile C, Sprint 4).

Frozen signature: ``docs/refactor/CONTRACTS.md`` §4 — one function,
``search_knowledge(ctx, query)``.

Non-negotiable rule 12: *knowledge retrieval and conversation memory never
substitute for a Cube query on a live metric value.* That is enforced by
construction here, not by prompt instruction:

* this module writes **zero artifacts** — nothing it returns can be cited as
  evidence by ``EvidenceValidator``, because there is no artifact id to cite;
* it returns document text and citations, and the only numbers that can appear
  in a summary are the ones an operator typed into a document.

``CONTRACTS.md`` §2 explicitly permits Knowledge to return ``success=True``
with zero artifacts ("a knowledge-search miss"), which is what makes that
enforceable rather than a violation of the envelope's success rule.

Rule 5 is a different matter here than in Analytics. ``search_knowledge``
takes a *query string*, not ``evidence_ids`` — it is a retrieval tool, not a
calculation over evidence, so the "tools never fetch" idiom does not transfer.
What it must not do is fetch a *metric*, and it cannot: it has no MCP client
call in it.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.intent import judge_relevance
from seleric_swarm.agent.limits import withdraw_tool
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import ArtifactProvenance
from seleric_swarm.knowledge.corpus import corpus_dir, load_corpus
from seleric_swarm.knowledge.search import search_documents
from seleric_swarm.toolsets import policy_config as policy

_CALCULATION_VERSION = "knowledge.v1"
_MAX_KNOWLEDGE_SEARCHES = 2


async def search_knowledge(ctx: RunContext[SelericDeps], query: str) -> ToolResult:
    """Retrieve operator documents — incidents, SOPs, model cards, experiment notes.

    Three outcomes, kept distinct on purpose because they tell an operator
    different things:

    * **corpus empty** — nobody has loaded any documents. ``success=True``
      with a named warning, because the *query* did not fail.
    * **no match** — documents exist, none are about this. Also
      ``success=True``; "we have nothing written down about that" is an
      answer.
    * **hits** — titles, types, citations and snippets, so the agent can quote
      a source it can name rather than paraphrasing something unattributable.
    """
    if not query.strip():
        return ToolResult(
            success=False,
            summary="empty query",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
        )

    # Live: "hi"/"thanks!" made 8-20 knowledge searches (105s). A second search
    # for the same mission never finds what the first missed.
    used = ctx.deps.call_counts.get("search_knowledge", 0) + 1
    ctx.deps.call_counts["search_knowledge"] = used
    if used > _MAX_KNOWLEDGE_SEARCHES:
        return ToolResult(
            success=True,
            summary=(
                "Knowledge search budget is spent for this mission. Do not search again; "
                "answer now with what you have."
            ),
            warnings=[policy.WARN_EMPTY_CORPUS],
            provenance=ArtifactProvenance(calculation_version=_CALCULATION_VERSION),
        )

    documents = load_corpus()
    if not documents:
        # An empty corpus is a deployment/data state, not a query problem: a
        # second search can never find what was never loaded (live L12: 3
        # searches after the first said "corpus is empty"). Withdraw the tool so
        # the model cannot re-search — same rule as an unavailable MCP capability.
        withdraw_tool(ctx.deps, "search_knowledge")
        return ToolResult(
            success=True,
            summary=(
                f"the knowledge corpus is empty — no documents have been loaded into "
                f"{policy.KNOWLEDGE_CORPUS_DIRNAME}/. This is not a failed search; there "
                f"is nothing written down yet. Do not search again or substitute a metric "
                f"value for a document (rule 12)."
            ),
            warnings=[policy.WARN_EMPTY_CORPUS],
            provenance=ArtifactProvenance(calculation_version=_CALCULATION_VERSION),
        )

    hits = search_documents(documents, query)
    if not hits:
        return ToolResult(
            success=True,
            summary=(
                f"no document in the {len(documents)}-document corpus matches {query!r}"
            ),
            provenance=ArtifactProvenance(calculation_version=_CALCULATION_VERSION),
        )

    lines = [
        f"{index}. {hit.document.citation()}\n   {hit.snippet()}"
        for index, hit in enumerate(hits, start=1)
    ]
    # #5: a relevance second opinion on the top lexical hit — keyword ranking
    # can surface a document that merely shares words. Fail-open; one Jev call,
    # top hit only. ponytail: top-1 only, judge more hits if false positives matter.
    relevant = await judge_relevance(
        query,
        hits[0].snippet(),
        base_url=ctx.deps.jev.base_url,
        api_key=ctx.deps.jev.api_key,
        timeout=ctx.deps.jev.timeout,
    )
    warnings = (
        ["top result may be a keyword match rather than an on-topic answer"]
        if relevant is False
        else []
    )
    return ToolResult(
        success=True,
        # No artifact_ids: a document is not evidence for a numeric claim.
        summary=f"{len(hits)} document(s) matching {query!r}:\n" + "\n".join(lines),
        warnings=warnings,
        provenance=ArtifactProvenance(
            calculation_version=_CALCULATION_VERSION,
            source_metadata={
                "corpus_dir": str(corpus_dir()),
                "corpus_size": len(documents),
                "citations": [h.document.citation() for h in hits],
                "matched_terms": sorted({t for h in hits for t in h.matched_terms}),
            },
        ),
    )

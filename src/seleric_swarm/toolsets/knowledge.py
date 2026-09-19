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
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.conversations.contracts import ArtifactProvenance
from seleric_swarm.knowledge.corpus import corpus_dir, load_corpus
from seleric_swarm.knowledge.search import search_documents
from seleric_swarm.toolsets import policy_config as policy

_CALCULATION_VERSION = "knowledge.v1"


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

    documents = load_corpus()
    if not documents:
        return ToolResult(
            success=True,
            summary=(
                f"the knowledge corpus is empty — no documents have been loaded into "
                f"{policy.KNOWLEDGE_CORPUS_DIRNAME}/. This is not a failed search; there "
                f"is nothing written down yet. Do not substitute a metric value for a "
                f"document (rule 12)."
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
    return ToolResult(
        success=True,
        # No artifact_ids: a document is not evidence for a numeric claim.
        summary=f"{len(hits)} document(s) matching {query!r}:\n" + "\n".join(lines),
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

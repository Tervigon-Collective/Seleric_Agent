"""Document retrieval for the Knowledge toolset (Profile C, Sprint 4).

A deliberately small, file-backed corpus: markdown documents on disk, ranked
by keyword. No database, no embedding service, no ``SelericDeps`` field.

Why not the hybrid search that already exists
---------------------------------------------
``conversations/phase7.py`` is a real, wired, lexical + pgvector + RRF engine,
and reusing it was the first instinct. Three facts ruled it out:

* it searches the Postgres ``artifacts``/``messages``/``memories`` tables,
  which ``ctx.deps.artifact_store`` (an in-process dict, ``state/artifacts.py``)
  does not write to — so it would never surface anything this agent produced;
* ``search()`` is synchronous, and tools are ``async``;
* it scopes by a six-member ``kind`` set with no ``artifact_type`` filter.

More decisively, it answers *"what did we discuss"*. The contract asks for
retrieval over incident reports, SOPs, model cards and experiment notes —
operator documents that nothing in this system writes. Those are files.

Rule 12 — knowledge retrieval never substitutes for a Cube query on a live
metric value — is enforced structurally rather than by convention: this
package returns text and citations, and ``toolsets/knowledge.py`` writes no
artifact at all.
"""

from seleric_swarm.knowledge.corpus import Document, load_corpus
from seleric_swarm.knowledge.search import Hit, search_documents

__all__ = ["Document", "Hit", "load_corpus", "search_documents"]

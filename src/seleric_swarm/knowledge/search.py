"""Keyword ranking over the knowledge corpus.

Deliberately not an embedding search. The corpus is operator documents
measured in dozens, not millions; a vector index would add a service
dependency and an approximation to a problem that exact term matching solves.
If the corpus ever grows past what this handles, ``conversations/phase7.py``
already has lexical + pgvector + reciprocal-rank fusion to graduate to.

Scoring is term-frequency with a title boost and a document-frequency
discount — a term appearing in every document distinguishes nothing, so it
contributes little. That is the useful half of TF-IDF without pretending to
be more principled than a corpus this size warrants.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from seleric_swarm.knowledge.corpus import Document
from seleric_swarm.toolsets import policy_config as policy

_WORD = re.compile(r"[a-z0-9_]+")

#: Words too common to discriminate. Kept short on purpose: an aggressive stop
#: list silently drops real query terms ("down", "rate", "drop" all matter
#: here), which is the failure mode that makes keyword search feel broken.
_STOP = frozenset({"the", "a", "an", "of", "and", "or", "to", "in", "is", "for", "on", "we"})

# A title match is worth this many body matches. Titles are short and
# deliberate, so a hit there is a much stronger signal of aboutness.
_TITLE_WEIGHT = 3.0


@dataclass
class Hit:
    document: Document
    score: float
    matched_terms: list[str] = field(default_factory=list)

    def snippet(self, limit: int = policy.KNOWLEDGE_SNIPPET_CHARS) -> str:
        """Text around the first matched term, so the reader sees *why* this
        document came back rather than just its opening lines."""
        text = self.document.text
        lowered = text.lower()
        start = 0
        for term in self.matched_terms:
            found = lowered.find(term)
            if found != -1:
                start = max(0, found - limit // 3)
                break
        excerpt = text[start : start + limit].strip()
        prefix = "…" if start > 0 else ""
        suffix = "…" if start + limit < len(text) else ""
        return f"{prefix}{excerpt}{suffix}"


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


def search_documents(
    documents: list[Document], query: str, *, limit: int = policy.KNOWLEDGE_MAX_RESULTS
) -> list[Hit]:
    """Rank documents against ``query``. Empty list when nothing matches.

    A zero-hit result is a legitimate answer, not a failure — the caller
    distinguishes "corpus is empty" from "corpus has documents, none match",
    because those tell an operator very different things.
    """
    terms = tokenize(query)
    if not terms or not documents:
        return []

    total_docs = len(documents)
    doc_frequency = {
        term: sum(1 for d in documents if term in d.text.lower() or term in d.title.lower())
        for term in set(terms)
    }

    hits: list[Hit] = []
    for document in documents:
        body = document.text.lower()
        title = document.title.lower()
        score = 0.0
        matched: list[str] = []
        for term in set(terms):
            occurrences = body.count(term)
            in_title = title.count(term)
            if not occurrences and not in_title:
                continue
            matched.append(term)
            # +1 inside the log keeps a term present in every document at a
            # small positive weight rather than exactly zero: it is weak
            # evidence, not anti-evidence.
            idf = math.log(1 + total_docs / (1 + doc_frequency[term]))
            score += (occurrences + in_title * _TITLE_WEIGHT) * idf
        if matched:
            # Longer documents accumulate incidental matches; damp by length so
            # a focused one-page SOP can outrank a sprawling incident log.
            score /= math.sqrt(max(len(body), 1))
            hits.append(Hit(document=document, score=score, matched_terms=sorted(matched)))

    hits.sort(key=lambda h: (-h.score, h.document.doc_id))
    return hits[:limit]

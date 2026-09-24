"""``RequiredScope`` — the hard constraints a query demands, resolved up front.

Why this exists
---------------
A mission can silently answer a *different* question than the one asked: the
model drops a requested breakdown/filter and nothing notices, so the answer
ships marked ``completed`` (live "Pro Suspender Boots by source, exclude
exchanges" trace — product filter and exchange exclusion both vanished). The
tool-layer guards (``toolsets/semantic.py::_reject_incompatible_dimensions``)
only fire on constraints the model *keeps*; a constraint the model never emits
is invisible to them.

``RequiredScope`` captures what the user demanded, resolved to catalogue
dimension ids, so the post-run reconciliation check
(``validation/signals.py::check_scope_coverage``) can prove the executed
evidence actually covers it — and force a REVISE (not a silent ``completed``)
when it does not.

Resolution happens in the runner (``agent/runner.py``), where the catalogue
bootstrap's alias index is reachable, and the result is frozen onto
``SelericDeps`` so the validation check stays synchronous and deterministic.

Scope of this version (deliberately narrow, high-precision)
-----------------------------------------------------------
Only **requested breakdowns** — ``"... by <dimension>"`` phrasing that resolves
to a real catalogue dimension — are captured and enforced. This is the class
with near-zero false positives: a term is kept *only* when it maps to a known
dimension, so an unresolved "by June" / "made by X" never manufactures a
constraint. Enforcing a missing breakdown is safe because you cannot answer
"by source" with a single aggregate row.

Deliberately NOT captured yet (they need catalogue *value/entity* resolution —
the metric-resolver work, tracked with the capability audit): named-entity
filters ("Pro Suspender Boots" → a product filter) and exclusion concepts
("exclude exchanges"). Both are extension points on ``RequiredScope``; adding
them must reuse ``catalogue_resolve_term`` and keep the same "only enforce what
resolves confidently" contract, or they will produce false REVISEs.

Fail-open: any resolution miss drops that term. An empty ``RequiredScope`` makes
the coverage check ``NOT_APPLICABLE`` — the mission runs exactly as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Breakdown lead-ins. "by" is the loose one, but precision comes from the
# resolve-or-drop rule below, not from this pattern: a captured term that does
# not map to a catalogue dimension is discarded, so a false "by" hit costs
# nothing. Capture up to 3 following words; resolution tries the longest
# n-gram first so "source last 5 days" → "source".
_BREAKDOWN_RE = re.compile(
    r"\b(?:split\s+by|broken\s+down\s+by|grouped?\s+by|group\s+by|breakdown\s+by|"
    r"by|per|across)\s+([A-Za-z][A-Za-z0-9 _/-]{0,40})",
    re.IGNORECASE,
)

# Words that never begin a dimension name — stop n-gram growth at them so a
# capture like "source last 5 days" collapses to "source".
_STOPWORDS = frozenset(
    {"last", "this", "next", "the", "a", "an", "for", "in", "on", "over", "and", "of", "to"}
)


@dataclass(frozen=True)
class RequiredScope:
    """Hard constraints resolved to catalogue dimension ids.

    ``breakdowns`` — dimension ids the user asked to break the answer down by.
    (Extension points, not yet populated: filter dims from named entities,
    exclusion dims.)
    """

    breakdowns: frozenset[str] = frozenset()

    def is_empty(self) -> bool:
        return not self.breakdowns


def _normalize(text: str) -> str:
    return text.strip().lower().replace("-", " ").replace("_", " ")


def _resolve_dimension(
    term: str, alias_index: dict[str, str], dimension_ids: frozenset[str]
) -> str | None:
    """Map a business term to a catalogue dimension id, or None. Confident
    matches only — an alias hit or an exact id; never a fuzzy guess (a wrong
    dimension here forces a needless REVISE)."""
    key = _normalize(term)
    if not key:
        return None
    if key in alias_index:  # alias / display token → dimension id
        return alias_index[key]
    as_id = key.replace(" ", "_")
    if as_id in dimension_ids:
        return as_id
    return None


def _candidate_terms(query: str) -> list[str]:
    """Every breakdown capture, expanded to its longest→shortest leading
    n-grams (stopping at a stopword) so resolution can try the fullest phrase
    first, then fall back to the head noun."""
    terms: list[str] = []
    for match in _BREAKDOWN_RE.finditer(query):
        words = match.group(1).split()
        kept: list[str] = []
        for w in words[:3]:
            if _normalize(w) in _STOPWORDS:
                break
            kept.append(w)
        # longest first: ["source","name"] -> "source name", then "source"
        for n in range(len(kept), 0, -1):
            terms.append(" ".join(kept[:n]))
    return terms


def build_required_scope(
    query: str,
    *,
    alias_index: dict[str, str] | None,
    dimension_ids: frozenset[str] | set[str] | None,
) -> RequiredScope:
    """Resolve a query's requested breakdowns to catalogue dimension ids.

    ``alias_index`` / ``dimension_ids`` come from the catalogue bootstrap
    (``CatalogueBootstrap.alias_index()`` / ``dimension_ids()``). Both empty →
    an empty scope (fail-open: the coverage check becomes NOT_APPLICABLE)."""
    aliases = alias_index or {}
    dims = frozenset(dimension_ids or ())
    if not aliases and not dims:
        return RequiredScope()
    resolved: set[str] = set()
    for term in _candidate_terms(query):
        dim = _resolve_dimension(term, aliases, dims)
        if dim:
            resolved.add(dim)
    return RequiredScope(breakdowns=frozenset(resolved))


def _demo() -> None:
    """Self-check — runs the parser's decision points. `python -m ...scope`."""
    aliases = {"source": "source_name", "order source": "source_name", "vendor": "vendor"}
    dims = frozenset({"source_name", "vendor", "lt_platform"})

    # "by <dim>" resolves; trailing time words are stripped.
    s = build_required_scope("orders by source last 5 days", alias_index=aliases, dimension_ids=dims)
    assert s.breakdowns == frozenset({"source_name"}), s.breakdowns

    # exact dimension id via "per".
    s = build_required_scope("revenue per lt_platform", alias_index=aliases, dimension_ids=dims)
    assert s.breakdowns == frozenset({"lt_platform"}), s.breakdowns

    # unresolved "by" target manufactures nothing (precision safeguard).
    s = build_required_scope("orders by June 2026", alias_index=aliases, dimension_ids=dims)
    assert s.is_empty(), s.breakdowns

    # no breakdown phrasing at all.
    s = build_required_scope("total orders yesterday", alias_index=aliases, dimension_ids=dims)
    assert s.is_empty(), s.breakdowns

    # empty catalogue → fail-open empty scope.
    s = build_required_scope("orders by source", alias_index=None, dimension_ids=None)
    assert s.is_empty(), s.breakdowns

    print("scope demo ok")


if __name__ == "__main__":
    _demo()

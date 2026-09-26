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

**Named values** (``value_filters``) — a word in the question that the data
itself records as a value ("whatsapp" → ``lt_utm_medium``/``utm_medium`` =
whatsapp). Resolved by the gateway's ``catalogue_resolve_values``, which learns
every dimension's values from Cube (nothing is declared by hand). Same
confident-only contract: only a term that is *not* catalogue vocabulary and
matches a value *exactly* becomes a filter; partial matches (fuzzy, contained,
abbreviations) never do — they are only shown to the model as suggestions,
though an abbreviation found beside an exact match (``wa`` next to
``whatsapp``) is listed as one of that filter's values.

Still not captured: exclusion concepts ("exclude exchanges").

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
class ValueFilter:
    """A word the user said that the data records as a value.

    ``dimensions`` — every dimension holding that exact value (the same word can
    live on several views, e.g. ``lt_utm_medium`` and ``utm_medium``); evidence
    filtered or grouped by any one of them covers it. ``values`` — the exact
    spellings plus same-dimension abbreviations to filter on."""

    term: str
    dimensions: frozenset[str]
    values: tuple[str, ...]


@dataclass(frozen=True)
class RequiredScope:
    """Hard constraints resolved to catalogue dimension ids.

    ``breakdowns`` — one **candidate set** per requested breakdown term. A grain
    word is often ambiguous in the catalogue ("channel" → ``channel`` /
    ``lt_channel`` / ``acquisition_channel``, all real dimensions), so a term
    resolves to the *set* of dimensions that share its grain language rather than
    one arbitrarily-picked id. Coverage is satisfied when the answer groups by
    **any** id in each set — the guard still catches "no grouping at all", but no
    longer forces a REVISE when the model chose a valid sibling dimension.
    ``value_filters`` — named values the answer must be filtered to.
    (Extension point, not yet populated: exclusion dims.)
    """

    breakdowns: frozenset[frozenset[str]] = frozenset()
    value_filters: tuple[ValueFilter, ...] = ()

    def is_empty(self) -> bool:
        return not self.breakdowns and not self.value_filters


def value_filters_from_resolution(resolution: dict | None) -> tuple[ValueFilter, ...]:
    """Confident value filters from a ``catalogue_resolve_values`` payload:
    non-vocabulary terms with an exact match only."""
    filters: list[ValueFilter] = []
    for term in (resolution or {}).get("terms") or []:
        if term.get("catalogue_vocabulary") or term.get("best_match") != "exact":
            continue
        dims: set[str] = set()
        values: list[str] = []
        for d in term.get("dimensions") or []:
            matches = d.get("values") or []
            if not any(v.get("match") == "exact" for v in matches):
                continue
            dims.add(str(d.get("dimension")))
            for v in matches:
                if v.get("match") in ("exact", "abbreviation") and v.get("value") not in values:
                    values.append(str(v.get("value")))
        if dims:
            filters.append(
                ValueFilter(term=str(term.get("term")), dimensions=frozenset(dims), values=tuple(values))
            )
    return tuple(filters)


def _normalize(text: str) -> str:
    return text.strip().lower().replace("-", " ").replace("_", " ")


def _resolve_dimension_candidates(
    term: str, alias_index: dict[str, str], dimension_ids: frozenset[str]
) -> frozenset[str]:
    """Every catalogue dimension whose grain language matches *term*.

    A term maps to a dimension when it is that dimension's id or an alias
    (exact), or when the term's words are all tokens of a dimension id or one of
    its aliases — e.g. "channel" is a token of ``lt_channel`` and
    ``acquisition_channel``. This mirrors the ambiguity the catalogue's own
    dimension resolver reports (``channel`` vs ``lt_channel``); it is derived
    entirely from the catalogue's ids/aliases, with no per-term hardcoding.

    Returning the *set* (not one arbitrary pick) is deliberate: it only ever
    widens what coverage accepts, so it can relax a false REVISE but never
    manufacture a new failure — a query that grouped by nothing still matches
    no candidate."""
    key = _normalize(term)
    if not key:
        return frozenset()
    cands: set[str] = set()
    as_id = key.replace(" ", "_")
    if as_id in dimension_ids:
        cands.add(as_id)
    if key in alias_index:
        cands.add(alias_index[key])
    key_tokens = set(key.split())
    for dim in dimension_ids:
        if key_tokens <= set(_normalize(dim).split()):
            cands.add(dim)
    for alias_key, dim in alias_index.items():
        if key_tokens <= set(alias_key.split()):
            cands.add(dim)
    return frozenset(cands)


def _candidate_term_groups(query: str) -> list[list[str]]:
    """Per breakdown capture, its longest→shortest leading n-grams (stopping at
    a stopword) so resolution can try the fullest phrase first, then fall back to
    the head noun. One list per ``by <...>`` phrase in the query."""
    groups: list[list[str]] = []
    for match in _BREAKDOWN_RE.finditer(query):
        words = match.group(1).split()
        kept: list[str] = []
        for w in words[:3]:
            if _normalize(w) in _STOPWORDS:
                break
            kept.append(w)
        ngrams = [" ".join(kept[:n]) for n in range(len(kept), 0, -1)]
        if ngrams:
            groups.append(ngrams)
    return groups


def build_required_scope(
    query: str,
    *,
    alias_index: dict[str, str] | None,
    dimension_ids: frozenset[str] | set[str] | None,
) -> RequiredScope:
    """Resolve a query's requested breakdowns to catalogue dimension candidate sets.

    Each ``by <term>`` phrase becomes one candidate set (the dimensions sharing
    that grain language); the answer must group by any id in each set. The
    longest n-gram that resolves to at least one dimension wins per phrase, so
    "source name" is preferred over "source" but an unresolved "June" adds
    nothing.

    ``alias_index`` / ``dimension_ids`` come from the catalogue bootstrap
    (``CatalogueBootstrap.alias_index()`` / ``dimension_ids()``). Both empty →
    an empty scope (fail-open: the coverage check becomes NOT_APPLICABLE)."""
    aliases = alias_index or {}
    dims = frozenset(dimension_ids or ())
    if not aliases and not dims:
        return RequiredScope()
    resolved: set[frozenset[str]] = set()
    for ngrams in _candidate_term_groups(query):
        for term in ngrams:
            cands = _resolve_dimension_candidates(term, aliases, dims)
            if cands:
                resolved.add(cands)
                break
    return RequiredScope(breakdowns=frozenset(resolved))


def _demo() -> None:
    """Self-check — runs the parser's decision points. `python -m ...scope`."""
    aliases = {"source": "source_name", "order source": "source_name", "vendor": "vendor"}
    dims = frozenset({"source_name", "vendor", "lt_platform"})

    # "by <dim>" resolves to a single-candidate set; trailing time words stripped.
    s = build_required_scope("orders by source last 5 days", alias_index=aliases, dimension_ids=dims)
    assert s.breakdowns == frozenset({frozenset({"source_name"})}), s.breakdowns

    # exact dimension id via "per".
    s = build_required_scope("revenue per lt_platform", alias_index=aliases, dimension_ids=dims)
    assert s.breakdowns == frozenset({frozenset({"lt_platform"})}), s.breakdowns

    # Ambiguous grain: "channel" collects every sibling dimension, so an answer
    # grouped by any one of them satisfies coverage (the L4 fix).
    channel_dims = frozenset({"channel", "lt_channel", "acquisition_channel", "vendor"})
    s = build_required_scope("net revenue by channel", alias_index={}, dimension_ids=channel_dims)
    assert s.breakdowns == frozenset({frozenset({"channel", "lt_channel", "acquisition_channel"})}), s.breakdowns

    # unresolved "by" target manufactures nothing (precision safeguard).
    s = build_required_scope("orders by June 2026", alias_index=aliases, dimension_ids=dims)
    assert s.is_empty(), s.breakdowns

    # no breakdown phrasing at all.
    s = build_required_scope("total orders yesterday", alias_index=aliases, dimension_ids=dims)
    assert s.is_empty(), s.breakdowns

    # empty catalogue → fail-open empty scope.
    s = build_required_scope("orders by source", alias_index=None, dimension_ids=None)
    assert s.is_empty(), s.breakdowns

    # value filters: exact, non-vocabulary matches only.
    resolution = {
        "terms": [
            {
                "term": "whatsapp",
                "catalogue_vocabulary": False,
                "best_match": "exact",
                "dimensions": [
                    {"dimension": "lt_utm_medium", "values": [
                        {"value": "whatsapp", "match": "exact"},
                        {"value": "wa", "match": "abbreviation"},
                    ]},
                    {"dimension": "landing_page_path", "values": [{"value": "/?utm_medium=whatsapp", "match": "token"}]},
                ],
            },
            {"term": "google", "catalogue_vocabulary": True, "best_match": "exact", "dimensions": []},
            {"term": "month", "catalogue_vocabulary": False, "best_match": "token", "dimensions": []},
        ]
    }
    vf = value_filters_from_resolution(resolution)
    assert vf == (ValueFilter("whatsapp", frozenset({"lt_utm_medium"}), ("whatsapp", "wa")),), vf

    print("scope demo ok")


if __name__ == "__main__":
    _demo()

"""Value candidates → entities / filters / exclusions (no question-text scan)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from seleric_swarm.contracts.pipeline import EntitySpec, FilterSpec
from seleric_swarm.semantic import SemanticReadModel
from seleric_swarm.understand import QuestionDraft


class ValueHit(BaseModel):
    """One catalogue value hit for a span of the question. Never a filter by itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    term: str
    dimension: str
    value: str
    volume: int = 0
    match: str = "exact"
    source_span: str = ""
    catalogue_vocabulary: bool = False


class SpecCandidates(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    values: tuple[ValueHit, ...] = ()
    axes: tuple[tuple[str, str], ...] = ()


def candidates_from_resolution(resolution: dict[str, object] | None) -> SpecCandidates:
    """Wrap gateway ``catalogue_resolve_values`` payload into typed hits."""
    payload = resolution or {}
    values: list[ValueHit] = []
    terms = payload.get("terms")
    if not isinstance(terms, list):
        terms = []
    for term in terms:
        if not isinstance(term, dict):
            continue
        span = str(term.get("term") or "")
        vocab = bool(term.get("catalogue_vocabulary"))
        best = str(term.get("best_match") or "")
        dimensions = term.get("dimensions")
        if not isinstance(dimensions, list):
            continue
        for dim_block in dimensions:
            if not isinstance(dim_block, dict):
                continue
            dim = str(dim_block.get("dimension") or "")
            if not dim:
                continue
            raw_values = dim_block.get("values")
            if not isinstance(raw_values, list):
                continue
            for item in raw_values:
                if not isinstance(item, dict):
                    continue
                values.append(
                    ValueHit(
                        term=span,
                        dimension=dim,
                        value=str(item.get("value") or ""),
                        volume=int(item.get("volume") or 0),
                        match=str(item.get("match") or best or ""),
                        source_span=span,
                        catalogue_vocabulary=vocab,
                    )
                )
    axes_raw = payload.get("axes")
    axes_map = axes_raw if isinstance(axes_raw, dict) else {}
    axes = tuple(
        sorted((str(key), str(val)) for key, val in axes_map.items() if key and val)
    )
    return SpecCandidates(values=tuple(values), axes=axes)


def _best_hits(
    candidates: SpecCandidates,
    *,
    ordinary: frozenset[str],
    phrases: frozenset[str] | None = None,
) -> dict[tuple[str, str], ValueHit]:
    best: dict[tuple[str, str], ValueHit] = {}
    for hit in candidates.values:
        if hit.catalogue_vocabulary or hit.match not in ("exact", "abbreviation"):
            continue
        term_key = hit.term.strip().lower()
        if term_key in ordinary:
            continue
        span_key = hit.source_span.strip().lower()
        if phrases and term_key not in phrases and span_key not in phrases:
            continue
        key = (term_key, hit.dimension)
        prev = best.get(key)
        if prev is None or hit.volume > prev.volume:
            best[key] = hit
    return best


def pick_entities_and_filters(
    *,
    draft: QuestionDraft,
    candidates: SpecCandidates,
    read_model: SemanticReadModel,
) -> tuple[tuple[EntitySpec, ...], tuple[FilterSpec, ...], str | None]:
    """Build entities and filters. Returns optional clarification reason for ambiguity."""
    del read_model  # reserved for family-aware disambiguation (M1.4)
    ordinary = frozenset(word.strip().lower() for word in draft.ordinary_words if word)
    entity_phrases = frozenset(phrase.strip().lower() for phrase in draft.entity_phrases if phrase)
    named_filter_phrases = frozenset(
        phrase.strip().lower()
        for phrase in (*draft.entity_phrases, *(hit.term for hit in candidates.values))
        if phrase
    ) - ordinary

    best = _best_hits(candidates, ordinary=ordinary, phrases=None)
    # Ambiguity: same term, several dims, comparable volume → clarify (M1.4 owns polish).
    by_term: dict[str, list[ValueHit]] = {}
    for hit in best.values():
        if hit.volume <= 0:
            continue
        by_term.setdefault(hit.term.strip().lower(), []).append(hit)
    for term, hits in by_term.items():
        dims = {hit.dimension for hit in hits}
        if len(dims) > 1:
            volumes = sorted(hit.volume for hit in hits)
            if volumes[-1] > 0 and volumes[0] * 2 >= volumes[-1]:
                return (), (), f"ambiguous_value:{term}"

    entities: list[EntitySpec] = []
    filter_groups: dict[str, list[ValueHit]] = {}
    entity_dim = (draft.entity_dimension or "").strip()

    for hit in best.values():
        if hit.volume <= 0:
            continue
        term_key = hit.term.strip().lower()
        if draft.shape == "entity_comparison" and entity_dim and hit.dimension == entity_dim:
            span_key = hit.source_span.strip().lower()
            if entity_phrases and term_key not in entity_phrases and span_key not in entity_phrases:
                continue
            entities.append(
                EntitySpec(
                    dimension=hit.dimension,
                    values=(hit.value,),
                    source_span=hit.source_span,
                )
            )
            continue
        if term_key in ordinary:
            continue
        filter_groups.setdefault(hit.dimension, []).append(hit)

    # Collapse several values on one dimension into one FilterSpec(operator="in").
    filters: list[FilterSpec] = []
    for dimension, hits in filter_groups.items():
        values = tuple(dict.fromkeys(hit.value for hit in hits if hit.value))
        if not values:
            continue
        span = hits[0].source_span if len(hits) == 1 else ""
        volume = max(hit.volume for hit in hits)
        filters.append(
            FilterSpec(
                dimension=dimension,
                operator="in",
                values=values,
                source_span=span,
                volume=volume,
            )
        )

    # Merge entity rows on the same dimension into one EntitySpec.
    entity_by_dim: dict[str, list[str]] = {}
    entity_span: dict[str, str] = {}
    for entity in entities:
        entity_by_dim.setdefault(entity.dimension, []).extend(entity.values)
        entity_span.setdefault(entity.dimension, entity.source_span)
    merged_entities = tuple(
        EntitySpec(
            dimension=dim,
            values=tuple(dict.fromkeys(vals)),
            source_span=entity_span.get(dim, ""),
        )
        for dim, vals in entity_by_dim.items()
    )
    del named_filter_phrases
    return merged_entities, tuple(filters), None


def pick_exclusions(
    *,
    draft: QuestionDraft,
    candidates: SpecCandidates,
) -> tuple[FilterSpec, ...]:
    """Exclusions become ``not_in`` filters resolved against value candidates."""
    phrases = frozenset(phrase.strip().lower() for phrase in draft.exclusion_phrases if phrase)
    if not phrases:
        return ()
    ordinary = frozenset(word.strip().lower() for word in draft.ordinary_words if word)
    best = _best_hits(candidates, ordinary=ordinary, phrases=phrases)
    groups: dict[str, list[ValueHit]] = {}
    for hit in best.values():
        if hit.volume <= 0:
            continue
        term_key = hit.term.strip().lower()
        if term_key not in phrases and hit.source_span.strip().lower() not in phrases:
            continue
        groups.setdefault(hit.dimension, []).append(hit)
    out: list[FilterSpec] = []
    for dimension, hits in groups.items():
        values = tuple(dict.fromkeys(hit.value for hit in hits if hit.value))
        if values:
            out.append(
                FilterSpec(
                    dimension=dimension,
                    operator="not_in",
                    values=values,
                    source_span=hits[0].source_span if len(hits) == 1 else "",
                    volume=max(hit.volume for hit in hits),
                )
            )
    return tuple(out)

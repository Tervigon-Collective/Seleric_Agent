"""Map the names the understand model wrote back to catalogue ids.

The understand prompt lists catalogue labels, not ids, so a draft may carry "Net sales" where
the spec needs ``net_sales``. Matching is exact against the read model (id, or label compared
case- and space-insensitively); it never guesses what the user meant.
"""

from __future__ import annotations

from seleric_swarm.contracts.pipeline import QuestionDraft
from seleric_swarm.semantic import SemanticReadModel


def _key(text: str | None) -> str:
    return " ".join(str(text or "").replace("_", " ").casefold().split())


def _unique(index: dict[str, set[str]], *names: str | None) -> str | None:
    for name in names:
        hits = index.get(_key(name))
        if hits and len(hits) == 1:
            return next(iter(hits))
    return None


def _metric_index(read_model: SemanticReadModel) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for m in read_model.metrics:
        for name in (m.id, m.label):
            if name:
                index.setdefault(_key(name), set()).add(m.id)
    return index


def _dimension_index(read_model: SemanticReadModel) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for d in read_model.dimensions:
        for name in (d.id, d.extra.get("label") if d.extra else None):
            if name:
                index.setdefault(_key(str(name)), set()).add(d.id)
    return index


def resolve_draft_names(draft: QuestionDraft, read_model: SemanticReadModel) -> QuestionDraft:
    metrics = _metric_index(read_model)
    dims = _dimension_index(read_model)
    known_metrics = read_model.metric_ids()
    known_dims = read_model.dimension_ids()

    def metric(mid: str | None, words: str | None) -> str | None:
        if mid and mid in known_metrics:
            return mid
        # An unmatched name stays as written so the validator still reports it.
        return _unique(metrics, mid, words) or mid

    def dim(did: str | None) -> str | None:
        if did and did in known_dims:
            return did
        return _unique(dims, did) or did

    update: dict[str, object] = {
        "measures": tuple(m.model_copy(update={"metric_id": metric(m.metric_id, m.words)}) for m in draft.measures),
        "breakdown_dimensions": tuple(d for d in (dim(x) for x in draft.breakdown_dimensions) if d),
    }
    if draft.rank_by is not None:
        update["rank_by"] = draft.rank_by.model_copy(
            update={"metric_id": metric(draft.rank_by.metric_id, draft.rank_by.words)})
    if draft.entity_dimension:
        update["entity_dimension"] = dim(draft.entity_dimension)
    return draft.model_copy(update=update)

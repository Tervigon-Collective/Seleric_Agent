"""Intent fidelity: does the spec about to run still answer the question the user asked?

Recoveries between understand and plan (name resolution, overview/underspecified → exploration,
prior-spec merge) may rewrite the draft. A rewrite that drops a dimension, entity or measure the
user named turns a scoped question into a generic one, and the answer then reads as if it covered
the original ask. ``intent_lost`` lists what was dropped so the mission can fall back instead.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from seleric_swarm.contracts.pipeline import QuestionDraft, QuestionSpec, ResultSet
from seleric_swarm.contracts.pipeline._report import ReportSpec
from seleric_swarm.semantic import SemanticReadModel

_WORD = re.compile(r"[a-z0-9]+")
_FILLER = frozenset({"the", "of", "a", "an", "and", "by", "per", "for", "in", "on", "to", "with"})


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.casefold()) if t not in _FILLER}


def explicit_metric_ids(question: str, draft: QuestionDraft, read_model: SemanticReadModel) -> tuple[str, ...]:
    """Metrics whose words the user actually wrote (not ones the model chose to show)."""
    said = _tokens(question)
    known = read_model.metric_ids()
    out: list[str] = []
    for m in (*draft.measures, *((draft.rank_by,) if draft.rank_by is not None else ())):
        words = _tokens(m.words or "")
        if m.metric_id and m.metric_id in known and words and words <= said and m.metric_id not in out:
            out.append(m.metric_id)
    return tuple(out)


def scope_axes(axes: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Axes that narrow what is measured (``scope=product``); a date basis does not."""
    return tuple((k, v) for k, v in axes if k == "scope")


def asked_dimensions(draft: QuestionDraft, read_model: SemanticReadModel) -> tuple[str, ...]:
    """Catalogue dimensions the draft scoped the question by (entity + breakdowns, every objective)."""
    names = [draft.entity_dimension, *draft.breakdown_dimensions]
    for obj in draft.objectives:
        names += [obj.entity_dimension, *obj.breakdown_dimensions]
    known = read_model.dimension_ids()
    out: list[str] = []
    for name in names:
        dim = (name or "").strip()
        # Unknown names are the validator's to report; time axes are grain, not scope.
        if not dim or dim in out or (known and dim not in known) or read_model.is_time_dimension(dim):
            continue
        out.append(dim)
    return tuple(out)


def _executed_dimensions(spec: QuestionSpec, report_spec: ReportSpec | None) -> set[str]:
    dims = set(spec.breakdowns)
    dims.update(e.dimension for e in spec.entities)
    dims.update(f.dimension for f in spec.filters)
    if report_spec is not None:
        dims.update(e.dimension for e in report_spec.entities)
        dims.update(f.dimension for f in report_spec.filters)
        for section in report_spec.sections:
            dims.update(section.dimensions)
            dims.update(f.dimension for f in section.filters)
    return dims


def intent_lost(
    asked: QuestionDraft,
    spec: QuestionSpec,
    read_model: SemanticReadModel,
    *,
    report_spec: ReportSpec | None = None,
    defaulted_measures: bool = False,
    axes: Sequence[tuple[str, str]] = (),
) -> list[str]:
    """What ``asked`` named that ``spec`` no longer carries; empty when the spec is faithful.

    ``asked`` is the draft as understood (names resolved, before any recovery rewrote it).
    ``defaulted_measures``: the measures were filled from the headline set, not from the user.
    ``axes``: scope axes the question's own words bound (see ``scope_axes``).
    """
    reasons: list[str] = []
    executed = _executed_dimensions(spec, report_spec)
    for dim in asked_dimensions(asked, read_model):
        if not executed & read_model.family_members(dim):
            reasons.append(f"dimension:{dim}")

    has_values = bool(spec.entities) or any(f.operator == "in" for f in spec.filters)
    if report_spec is not None:
        has_values = has_values or bool(report_spec.entities) or any(
            f.operator == "in" for f in (*report_spec.filters, *(f for s in report_spec.sections for f in s.filters))
        )
    if asked.entity_phrases and not has_values:
        reasons.append("entity:" + ",".join(asked.entity_phrases))

    asked_words = [m.words for m in asked.measures if m.words]
    if asked.rank_by is not None and asked.rank_by.words:
        asked_words.append(asked.rank_by.words)
    if defaulted_measures and asked_words:
        reasons.append("measures:" + ",".join(asked_words))
    if defaulted_measures and axes:
        reasons.append("axes:" + ",".join(f"{k}={v}" for k, v in axes))
    # An overview's "which year?" does not change the headline; elsewhere the model asking means
    # the defaults are a guess at what was meant.
    if defaulted_measures and asked.needs_clarification and asked.kind != "overview":
        reasons.append("unread:model_asked")
    section_metrics = any(s.metric_ids for s in report_spec.sections) if report_spec is not None else False
    if not spec.measures and spec.ranking is None and not section_metrics:
        reasons.append("measures:none")
    return reasons


def scope_uncovered(
    dimensions: Sequence[str],
    metric_ids: Sequence[str],
    result_sets: Sequence[ResultSet],
    read_model: SemanticReadModel,
) -> list[str]:
    """Requested dimensions / explicitly named metrics that no successful read covered.

    A read that succeeded with no rows still covers its slice (an honest "no sales").
    """
    ok = [rs for rs in result_sets if rs.error is None]
    reasons: list[str] = []
    for dim in dimensions:
        family = read_model.family_members(dim)
        if not any(family & {*rs.breakdowns, *(f.dimension for f in rs.filters)} for rs in ok):
            reasons.append(f"dimension:{dim}")
    read_metrics = {rs.metric_id for rs in ok}
    for mid in metric_ids:
        if not read_metrics & {mid, *read_model.grain_twins_for(mid)}:
            reasons.append(f"metric:{mid}")
    return reasons
